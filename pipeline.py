import ee
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import telegram
import asyncio


# ── GEE Authentication ────────────────────────────────────────────────────────
def init_gee():
    if 'GEE_SERVICE_ACCOUNT_KEY' in os.environ:
        key_data = json.loads(os.environ['GEE_SERVICE_ACCOUNT_KEY'])
        credentials = ee.ServiceAccountCredentials(
            email=key_data['client_email'],
            key_data=json.dumps(key_data)
        )
        ee.Initialize(credentials=credentials, project=key_data.get('project_id'))
    else:
        # Fallback for local development
        try:
            ee.Initialize()
        except Exception:
            print("[INFO] Local credentials not found. Please authenticate.")
            ee.Authenticate()
            ee.Initialize()


# ── GeoJSON Helpers ───────────────────────────────────────────────────────────
def drop_z(geom):
    """Strip Z coordinates before passing to GEE."""
    if geom.geom_type == 'Polygon':
        return Polygon([(x, y) for x, y, *_ in geom.exterior.coords])
    elif geom.geom_type == 'MultiPolygon':
        return MultiPolygon([Polygon([(x, y) for x, y, *_ in p.exterior.coords]) for p in geom.geoms])
    return geom


def gdf_to_ee_fc(gdf, name_col):
    """Convert GeoDataFrame to GEE FeatureCollection."""
    features = []
    for _, row in gdf.iterrows():
        geom = ee.Geometry(row.geometry.__geo_interface__)
        feat = ee.Feature(geom, {'Plot_name': row[name_col]})
        features.append(feat)
    return ee.FeatureCollection(features)


# ── Satellite Image Detection ─────────────────────────────────────────────────
def get_latest_image(aoi):
    """Query last 14 days for a Sentinel-2 or Landsat 8/9 image with <20% cloud cover."""
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    end   = now.strftime("%Y-%m-%d")

    def prep_s2(img):
        scl = img.select('SCL')
        mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
        return img.updateMask(mask).divide(10000).select(
            ['B2', 'B3', 'B4', 'B5', 'B8', 'B11'],
            ['BLUE', 'GREEN', 'RED', 'RE1', 'NIR', 'SWIR1']
        ).set('system:time_start', img.get('system:time_start'))
        
    def prep_landsat(img):
        qa = img.select('QA_PIXEL')
        cloudShadowBitMask = (1 << 4)
        cloudsBitMask = (1 << 3)
        dilatedCloudBitMask = (1 << 1)
        mask = qa.bitwiseAnd(cloudShadowBitMask).eq(0) \
                 .And(qa.bitwiseAnd(cloudsBitMask).eq(0)) \
                 .And(qa.bitwiseAnd(dilatedCloudBitMask).eq(0))
        opticalBands = img.select(['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6']).multiply(0.0000275).add(-0.2)
        # We duplicate SR_B4 for RE1 since Landsat lacks RedEdge
        return opticalBands.updateMask(mask).select(
            ['SR_B2', 'SR_B3', 'SR_B4', 'SR_B4', 'SR_B5', 'SR_B6'],
            ['BLUE', 'GREEN', 'RED', 'RE1', 'NIR', 'SWIR1']
        ).set('system:time_start', img.get('system:time_start'))

    s2_col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
          .filterBounds(aoi)
          .filterDate(start, end)
          .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
          .map(prep_s2)
    )
    
    l8_col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
          .filterBounds(aoi)
          .filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 20))
          .map(prep_landsat)
    )
    
    l9_col = (
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
          .filterBounds(aoi)
          .filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 20))
          .map(prep_landsat)
    )

    merged_col = s2_col.merge(l8_col).merge(l9_col).sort("system:time_start", False)

    if merged_col.size().getInfo() == 0:
        print("[INFO] No new images in last 14 days. Exiting.")
        sys.exit(0)
        
    image = merged_col.first()
    date_str = ee.Date(image.get("system:time_start")).format("YYYY-MM-dd").getInfo()
    print(f"[INFO] Latest image date: {date_str}")
    return image, date_str


# ── Index Computation (server-side on GEE) ────────────────────────────────────
def process_image(img, aoi):
    """Compute 6 vegetation indices and resample to 2m resolution."""
    # Resample to 2m before computing indices for smooth boundaries
    # We must get the projection from a specific band because Sentinel-2/Landsat bands have different native scales.
    crs = img.select('NIR').projection()
    img_2m = img.resample('bilinear').reproject(crs=crs, scale=2).clip(aoi)

    ndvi = img_2m.normalizedDifference(['NIR', 'RED']).rename('NDVI')
    ndmi = img_2m.normalizedDifference(['NIR', 'SWIR1']).rename('NDMI')
    ndwi = img_2m.normalizedDifference(['GREEN', 'NIR']).rename('NDWI')
    ndre = img_2m.normalizedDifference(['NIR', 'RE1']).rename('NDRE')
    evi  = img_2m.expression(
        '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))',
        {'NIR': img_2m.select('NIR'), 'RED': img_2m.select('RED'), 'BLUE': img_2m.select('BLUE')}
    ).rename('EVI')
    gci  = img_2m.expression(
        '(NIR / GREEN) - 1',
        {'NIR': img_2m.select('NIR'), 'GREEN': img_2m.select('GREEN')}
    ).rename('GCI')

    return img_2m.addBands([ndvi, ndmi, ndwi, ndre, evi, gci])


# ── Colour Palette Configuration ──────────────────────────────────────────────
# Ranges are crop-realistic (not -1 to 1) so the full colour gradient
# stretches across actual farm pixel values, revealing subtle variation.
# Palettes follow remote-sensing conventions: NASA MODIS, ESA Copernicus,
# and QGIS ColorBrewer standards.
INDEX_CONFIG = {
    # Red (stressed) → Yellow (moderate) → Dark Green (dense healthy crop)
    'NDVI': {
        'min': 0.1, 'max': 0.9,
        'palette': [
            '#a50026', '#d73027', '#f46d43', '#fdae61',
            '#fee090', '#fffab5', '#d9ef8b', '#a6d96a',
            '#66bd63', '#1a9850', '#006837'
        ]
    },
    # Dark Red (drought) → Yellow (moderate) → Dark Blue (waterlogged)
    'NDMI': {
        'min': -0.4, 'max': 0.6,
        'palette': [
            '#8c0d25', '#c1440e', '#e87233', '#f5b26b',
            '#ffffcc', '#c7e9b4', '#7fcdbb', '#41b6c4',
            '#1d91c0', '#225ea8', '#0c2c84'
        ]
    },
    # Brown (very dry soil) → White (dry vegetation) → Teal (water body)
    'NDWI': {
        'min': -0.3, 'max': 0.4,
        'palette': [
            '#543005', '#8c510a', '#bf812d', '#dfc27d',
            '#f6e8c3', '#f5f5f5', '#c7eae5', '#80cdc1',
            '#35978f', '#01665e', '#003c30'
        ]
    },
    # Red Edge chlorophyll: Red (low) → Yellow → Dark Green (high canopy)
    'NDRE': {
        'min': 0.1, 'max': 0.7,
        'palette': [
            '#a50026', '#d73027', '#f46d43', '#fdae61',
            '#fee090', '#fffab5', '#d9ef8b', '#a6d96a',
            '#66bd63', '#1a9850', '#006837'
        ]
    },
    # Enhanced Vegetation Index — less atmosphere noise than NDVI
    'EVI': {
        'min': 0.1, 'max': 0.7,
        'palette': [
            '#a50026', '#d73027', '#f46d43', '#fdae61',
            '#fee090', '#fffab5', '#d9ef8b', '#a6d96a',
            '#66bd63', '#1a9850', '#006837'
        ]
    },
    # Green Chlorophyll Index — pale yellow (low) → deep blue (high)
    'GCI': {
        'min': 0.5, 'max': 8.0,
        'palette': [
            '#ffffd9', '#edf8b1', '#c7e9b4', '#7fcdbb',
            '#41b6c4', '#2c7fb8', '#253494'
        ]
    },
}


# ── PNG Rendering ─────────────────────────────────────────────────────────────
def fetch_index_png(processed_image, index_name, plot_geom, cfg):
    """Render a single index heatmap PNG via GEE thumbnail endpoint."""
    url = processed_image.select(index_name).getThumbURL({
        'region': plot_geom,
        'dimensions': 512,
        'format': 'png',
        'min': cfg['min'],
        'max': cfg['max'],
        'palette': cfg['palette']
    })
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert('RGB')


def build_combined_png(processed_image, plot_name, date_str, plot_geom):
    """Stitch 6 index panels into a 2×3 grid with title bar and colour legends."""
    PANEL = 512
    LABEL = 60
    TITLE = 70
    ORDER = ['NDVI', 'NDMI', 'NDWI', 'NDRE', 'EVI', 'GCI']

    try:
        font_title = ImageFont.load_default(size=32)
        font_label = ImageFont.load_default(size=24)
        font_small = ImageFont.load_default(size=16)
    except Exception:
        font_title = ImageFont.load_default()
        font_label = ImageFont.load_default()
        font_small = ImageFont.load_default()

    panels = []
    for idx_name in ORDER:
        cfg = INDEX_CONFIG[idx_name]
        try:
            panel = fetch_index_png(processed_image, idx_name, plot_geom, cfg)
            panel = panel.resize((PANEL, PANEL))
        except Exception as e:
            print(f"[WARN] {idx_name} failed for {plot_name}: {e}")
            panel = Image.new('RGB', (PANEL, PANEL), color='#444444')

        draw = ImageDraw.Draw(panel)
        # Black label bar at bottom of each panel
        draw.rectangle([0, PANEL - LABEL, PANEL, PANEL], fill='#000000')
        draw.text((16, PANEL - LABEL + 15), idx_name, fill='white', font=font_label)

        # Colour legend bar with min / max labels
        legend_x = 120
        legend_y = PANEL - LABEL + 28
        legend_w = 320
        legend_h = 12
        palette   = cfg['palette']
        draw.text((legend_x - 46, legend_y - 4), str(cfg['min']), fill='#cccccc', font=font_small)
        segment_w = legend_w / len(palette)
        for j, color in enumerate(palette):
            draw.rectangle(
                [legend_x + j * segment_w, legend_y,
                 legend_x + (j + 1) * segment_w, legend_y + legend_h],
                fill=color
            )
        draw.text((legend_x + legend_w + 8, legend_y - 4), str(cfg['max']), fill='#cccccc', font=font_small)

        panels.append(panel)

    # Assemble 2-column × 3-row grid
    grid = Image.new('RGB', (PANEL * 2, PANEL * 3))
    for i, panel in enumerate(panels):
        grid.paste(panel, ((i % 2) * PANEL, (i // 2) * PANEL))

    # Title bar
    title = Image.new('RGB', (PANEL * 2, TITLE), color='#1a1a1a')
    draw  = ImageDraw.Draw(title)
    draw.text((20, 18), f"{plot_name}  \u2022  {date_str}", fill='white', font=font_title)

    # Final composite
    final = Image.new('RGB', (PANEL * 2, PANEL * 3 + TITLE))
    final.paste(title, (0, 0))
    final.paste(grid,  (0, TITLE))

    buf = BytesIO()
    final.save(buf, format='PNG')
    buf.seek(0)
    return buf


# ── Telegram Delivery ─────────────────────────────────────────────────────────
async def deliver_all(gdf, name_col, processed_image, date_str):
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id   = os.environ.get('TELEGRAM_CHAT_ID')

    if not bot_token or not chat_id:
        print("[WARN] Telegram credentials not set. Skipping delivery.")
        bot = None
    else:
        bot = telegram.Bot(token=bot_token)

    for _, row in gdf.iterrows():
        plot_name = str(row[name_col])
        plot_geom = ee.Geometry(row.geometry.__geo_interface__)
        print(f"[INFO] Rendering {plot_name}...")
        try:
            png_buf = build_combined_png(processed_image, plot_name, date_str, plot_geom)
            if bot:
                caption = f"\U0001f6f0 {plot_name}\n\U0001f4c5 {date_str}"
                await bot.send_photo(chat_id=chat_id, photo=png_buf, caption=caption)
                print(f"[INFO] Sent Telegram message for {plot_name}")
            else:
                print(f"[INFO] Rendered PNG for {plot_name} (no Telegram configured).")
        except Exception as e:
            print(f"[ERROR] Failed to deliver {plot_name}: {e}")


# ── Entry Point ───────────────────────────────────────────────────────────────
def main():
    try:
        init_gee()
        print("[INFO] GEE authenticated successfully.")
    except Exception as e:
        print(f"[ERROR] GEE Authentication failed: {e}")
        sys.exit(1)

    try:
        gdf = gpd.read_file("data/farms.geojson")
        gdf['geometry'] = gdf['geometry'].apply(drop_z)
        name_col = 'Plot_name' if 'Plot_name' in gdf.columns else gdf.columns[0]
        aoi = gdf_to_ee_fc(gdf, name_col)
    except Exception as e:
        print(f"[ERROR] Loading geometry failed: {e}")
        sys.exit(1)

    try:
        image, date_str = get_latest_image(aoi)
    except SystemExit:
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] Getting latest image failed: {e}")
        sys.exit(1)

    try:
        processed_image = process_image(image, aoi)
    except Exception as e:
        print(f"[ERROR] Processing image failed: {e}")
        sys.exit(1)

    asyncio.run(deliver_all(gdf, name_col, processed_image, date_str))
    print(f"[INFO] Pipeline complete. {len(gdf)} plots processed.")


if __name__ == "__main__":
    main()
