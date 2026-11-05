import ee
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timedelta, timezone

import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import telegram
import asyncio

from storage import (
    connect,
    has_observation,
    image_is_complete,
    save_observation,
    update_delivery_status,
)


CLOUD_SCORE_THRESHOLD = float(os.environ.get("CLOUD_SCORE_THRESHOLD", "0.60"))
if not 0 <= CLOUD_SCORE_THRESHOLD <= 1:
    raise ValueError("CLOUD_SCORE_THRESHOLD must be between 0 and 1.")

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
    """Find the latest cloud-filtered Sentinel-2 or Landsat 8/9 image."""
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    end   = now.strftime("%Y-%m-%d")

    def prep_s2(img):
        cloud_score = ee.Image(img.get("cloud_score")).select("cs_cdf")
        mask = cloud_score.gte(CLOUD_SCORE_THRESHOLD)
        return img.updateMask(mask).divide(10000).select(
            ['B2', 'B3', 'B4', 'B5', 'B8', 'B11'],
            ['BLUE', 'GREEN', 'RED', 'RE1', 'NIR', 'SWIR1']
        ).set({
            "system:time_start": img.get("system:time_start"),
            "satellite": "Sentinel-2",
            "farm_monitor_key": ee.String("Sentinel-2:").cat(
                ee.String(img.get("system:index"))
            ),
        })

    def prep_landsat(img, satellite):
        qa = img.select('QA_PIXEL')
        clear_mask = (
            qa.bitwiseAnd(1 << 0).eq(0)
            .And(qa.bitwiseAnd(1 << 1).eq(0))
            .And(qa.bitwiseAnd(1 << 2).eq(0))
            .And(qa.bitwiseAnd(1 << 3).eq(0))
            .And(qa.bitwiseAnd(1 << 4).eq(0))
            .And(qa.bitwiseAnd(1 << 5).eq(0))
        )
        optical_bands = (
            img.select(['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6'])
            .multiply(0.0000275)
            .add(-0.2)
        )
        return optical_bands.updateMask(clear_mask).select(
            ['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6'],
            ['BLUE', 'GREEN', 'RED', 'NIR', 'SWIR1']
        ).set({
            "system:time_start": img.get("system:time_start"),
            "satellite": satellite,
            "farm_monitor_key": ee.String(satellite).cat(":").cat(
                ee.String(img.get("system:index"))
            ),
        })

    s2_raw = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
          .filterBounds(aoi)
          .filterDate(start, end)
          .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
    )
    cloud_score_col = (
        ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start, end)
    )
    s2_col = ee.ImageCollection(
        ee.Join.saveFirst("cloud_score").apply(
            primary=s2_raw,
            secondary=cloud_score_col,
            condition=ee.Filter.equals(
                leftField="system:index",
                rightField="system:index",
            ),
        )
    ).filter(ee.Filter.notNull(["cloud_score"])).map(prep_s2)

    l8_col = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
          .filterBounds(aoi)
          .filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 30))
          .map(lambda img: prep_landsat(img, "Landsat 8"))
    )
    l9_col = (
        ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
          .filterBounds(aoi)
          .filterDate(start, end)
          .filter(ee.Filter.lt("CLOUD_COVER", 30))
          .map(lambda img: prep_landsat(img, "Landsat 9"))
    )

    merged_col = s2_col.merge(l8_col).merge(l9_col).sort("system:time_start", False)

    if merged_col.size().getInfo() == 0:
        print("[INFO] No suitable image found in the last 14 days.")
        return None, None, None, None
        
    image = merged_col.first()
    date_str = ee.Date(image.get("system:time_start")).format("YYYY-MM-dd").getInfo()
    satellite = image.get("satellite").getInfo()
    image_key = image.get("farm_monitor_key").getInfo()
    print(f"[INFO] Latest image date: {date_str}")
    print(f"[INFO] Satellite: {satellite}")
    return image, date_str, satellite, image_key


# ── Index Computation (server-side on GEE) ────────────────────────────────────
def process_image(img, aoi, satellite):
    """Compute available vegetation indices on a 5 m grid."""
    crs = img.select('NIR').projection()
    img_5m = img.resample('bilinear').reproject(crs=crs, scale=5).clip(aoi)

    ndvi = img_5m.normalizedDifference(['NIR', 'RED']).rename('NDVI')
    ndmi = img_5m.normalizedDifference(['NIR', 'SWIR1']).rename('NDMI')
    ndwi = img_5m.normalizedDifference(['GREEN', 'NIR']).rename('NDWI')
    evi  = img_5m.expression(
        '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))',
        {'NIR': img_5m.select('NIR'), 'RED': img_5m.select('RED'), 'BLUE': img_5m.select('BLUE')}
    ).rename('EVI')
    gci  = img_5m.expression(
        '(NIR / GREEN) - 1',
        {'NIR': img_5m.select('NIR'), 'GREEN': img_5m.select('GREEN')}
    ).rename('GCI')

    indices = [ndvi, ndmi, ndwi]
    if satellite == "Sentinel-2":
        indices.append(img_5m.normalizedDifference(['NIR', 'RE1']).rename('NDRE'))
    indices.extend([evi, gci])
    return img_5m.addBands(indices)


# ── Colour Palette Configuration ──────────────────────────────────────────────
# Ranges are crop-realistic (not -1 to 1) so the full colour gradient
# stretches across actual farm pixel values, revealing subtle variation.
# Palettes follow remote-sensing conventions: NASA MODIS, ESA Copernicus,
# and QGIS ColorBrewer standards.
INDEX_CONFIG = {
    # Red (stressed) → Yellow (moderate) → Dark Green (dense healthy crop)
    'NDVI': {
        'min': 0.0, 'max': 0.9,
        'palette': [
            '#a50026', '#d73027', '#f46d43', '#fdae61',
            '#fee090', '#fffab5', '#d9ef8b', '#a6d96a',
            '#66bd63', '#1a9850', '#006837'
        ]
    },
    # Dark Red (drought) → Yellow (moderate) → Dark Blue (waterlogged)
    'NDMI': {
        'min': -0.6, 'max': 0.6,
        'palette': [
            '#8c0d25', '#c1440e', '#e87233', '#f5b26b',
            '#ffffcc', '#c7e9b4', '#7fcdbb', '#41b6c4',
            '#1d91c0', '#225ea8', '#0c2c84'
        ]
    },
    # Brown (very dry soil) → White (dry vegetation) → Teal (water body)
    'NDWI': {
        'min': -0.3, 'max': 0.5,
        'palette': [
            '#543005', '#8c510a', '#bf812d', '#dfc27d',
            '#f6e8c3', '#f5f5f5', '#c7eae5', '#80cdc1',
            '#35978f', '#01665e', '#003c30'
        ]
    },
    # Red Edge chlorophyll: Red (low) → Yellow → Dark Green (high canopy)
    'NDRE': {
        'min': 0.0, 'max': 0.7,
        'palette': [
            '#a50026', '#d73027', '#f46d43', '#fdae61',
            '#fee090', '#fffab5', '#d9ef8b', '#a6d96a',
            '#66bd63', '#1a9850', '#006837'
        ]
    },
    # Enhanced Vegetation Index — less atmosphere noise than NDVI
    'EVI': {
        'min': 0.0, 'max': 0.7,
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


def build_combined_png(processed_image, plot_name, date_str, plot_geom, index_names):
    """Render every available index; a failed panel fails the whole report."""
    PANEL = 512
    LABEL = 60
    TITLE = 70

    try:
        font_title = ImageFont.load_default(size=32)
        font_label = ImageFont.load_default(size=24)
        font_small = ImageFont.load_default(size=16)
    except Exception:
        font_title = ImageFont.load_default()
        font_label = ImageFont.load_default()
        font_small = ImageFont.load_default()

    panels = []
    for idx_name in index_names:
        cfg = INDEX_CONFIG[idx_name]
        panel = fetch_index_png(processed_image, idx_name, plot_geom, cfg)
        panel = panel.resize((PANEL, PANEL))

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

    # Keep the same two-column layout while omitting unavailable indices.
    rows = math.ceil(len(panels) / 2)
    grid = Image.new('RGB', (PANEL * 2, PANEL * rows), color='#1a1a1a')
    for i, panel in enumerate(panels):
        grid.paste(panel, ((i % 2) * PANEL, (i // 2) * PANEL))

    # Title bar
    title = Image.new('RGB', (PANEL * 2, TITLE), color='#1a1a1a')
    draw  = ImageDraw.Draw(title)
    draw.text((20, 18), f"{plot_name}  \u2022  {date_str}", fill='white', font=font_title)

    # Final composite
    final = Image.new('RGB', (PANEL * 2, PANEL * rows + TITLE))
    final.paste(title, (0, 0))
    final.paste(grid,  (0, TITLE))

    buf = BytesIO()
    final.save(buf, format='PNG')
    buf.seek(0)
    return buf


def compute_plot_means(processed_image, plot_geom, index_names):
    """Fetch one mean value per available index for a plot."""
    band_names = [name.upper() for name in index_names]
    means = processed_image.select(band_names).reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=plot_geom,
        scale=5,
        maxPixels=10000000,
        bestEffort=True,
    ).getInfo()
    return {
        name.lower(): means.get(name.upper())
        for name in index_names
    }


def save_report(png_buffer, plot_name, date_str, image_key):
    report_dir = Path(
        os.environ.get("FARM_REPORT_DIR", "data/reports")
    )
    if not report_dir.is_absolute():
        report_dir = Path(__file__).resolve().parent / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", plot_name).strip("_") or "plot"
    key_suffix = hashlib.sha256(image_key.encode("utf-8")).hexdigest()[:10]
    report_path = report_dir / f"{safe_name}_{date_str}_{key_suffix}.png"
    report_path.write_bytes(png_buffer.getvalue())
    try:
        return report_path.resolve().relative_to(Path(__file__).resolve().parent).as_posix()
    except ValueError:
        return str(report_path.resolve())


# ── Telegram Delivery ─────────────────────────────────────────────────────────
async def deliver_all(
    gdf,
    name_col,
    processed_image,
    date_str,
    satellite,
    image_key,
    connection,
):
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id   = os.environ.get('TELEGRAM_CHAT_ID')

    bot_enabled = bool(bot_token and chat_id)
    if not bot_enabled:
        print("[INFO] Telegram credentials are not configured; reports will be saved locally.")
        if bool(bot_token) != bool(chat_id):
            print("[WARN] Set both Telegram credentials to enable delivery.")

    bot = None
    delivery_init_error = None
    if bot_enabled:
        candidate = telegram.Bot(token=bot_token)
        try:
            await candidate.initialize()
            bot = candidate
        except Exception as error:
            delivery_init_error = str(error)
            print(
                "[ERROR] Telegram initialization failed; reports will still be "
                f"saved locally: {error}"
            )
            try:
                await candidate.shutdown()
            except Exception as shutdown_error:
                print(f"[ERROR] Telegram cleanup failed: {shutdown_error}")

    index_names = ["NDVI", "NDMI", "NDWI", "EVI", "GCI"]
    if satellite == "Sentinel-2":
        index_names.insert(3, "NDRE")

    failed_plots = []

    async def process_plots(bot):
        for _, row in gdf.iterrows():
            plot_name = str(row[name_col])
            if has_observation(connection, plot_name, image_key):
                print(f"[INFO] Already recorded {plot_name} for {image_key}; skipping.")
                continue

            plot_geom = ee.Geometry(row.geometry.__geo_interface__)
            print(f"[INFO] Rendering {plot_name}...")
            try:
                means = compute_plot_means(processed_image, plot_geom, index_names)
                if means.get("ndvi") is None:
                    raise ValueError("No clear NDVI pixels are available for this plot.")
                png_buf = build_combined_png(
                    processed_image, plot_name, date_str, plot_geom, index_names
                )
                report_path = save_report(png_buf, plot_name, date_str, image_key)
            except Exception as error:
                print(f"[ERROR] Report failed for {plot_name}; it was not recorded: {error}")
                failed_plots.append(plot_name)
                continue

            observation = {
                "plot_name": plot_name,
                "image_key": image_key,
                "image_date": date_str,
                "satellite": satellite,
                "report_path": report_path,
                "delivery_status": (
                    "pending"
                    if bot
                    else "failed" if delivery_init_error else "not_configured"
                ),
                "delivery_error": delivery_init_error,
                **means,
            }
            try:
                save_observation(connection, observation)
            except Exception as error:
                print(f"[ERROR] Could not save results for {plot_name}: {error}")
                failed_plots.append(plot_name)
                continue

            if bot:
                caption = f"\U0001f6f0 {plot_name}\n\U0001f4c5 {date_str}"
                try:
                    png_buf.seek(0)
                    await bot.send_photo(chat_id=chat_id, photo=png_buf, caption=caption)
                    update_delivery_status(connection, plot_name, image_key, "sent")
                    print(f"[INFO] Sent Telegram report for {plot_name}")
                except Exception as error:
                    update_delivery_status(
                        connection, plot_name, image_key, "failed", str(error)
                    )
                    print(f"[ERROR] Telegram delivery failed for {plot_name}: {error}")
            else:
                print(f"[INFO] Saved report and index history for {plot_name}.")

    try:
        await process_plots(bot)
    finally:
        if bot:
            await bot.shutdown()

    return failed_plots


# ── Entry Point ───────────────────────────────────────────────────────────────
def run_pipeline(connection):
    try:
        init_gee()
        print("[INFO] GEE authenticated successfully.")
    except Exception as e:
        print(f"[ERROR] GEE Authentication failed: {e}")
        sys.exit(1)

    try:
        farms_path = Path(__file__).resolve().parent / "data" / "farms.geojson"
        gdf = gpd.read_file(farms_path)
        gdf['geometry'] = gdf['geometry'].apply(drop_z)
        name_col = 'Plot_name' if 'Plot_name' in gdf.columns else gdf.columns[0]
        if gdf.empty:
            raise ValueError("Farm GeoJSON contains no plots.")
        if gdf[name_col].isna().any() or gdf[name_col].astype(str).duplicated().any():
            raise ValueError("Farm plot names must be present and unique.")
        gdf[name_col] = gdf[name_col].astype(str)
        aoi = gdf_to_ee_fc(gdf, name_col)
    except Exception as e:
        print(f"[ERROR] Loading geometry failed: {e}")
        sys.exit(1)

    try:
        image, date_str, satellite, image_key = get_latest_image(aoi)
    except Exception as e:
        print(f"[ERROR] Getting latest image failed: {e}")
        sys.exit(1)

    if image is None:
        return

    plot_names = gdf[name_col].tolist()
    if image_is_complete(connection, image_key, plot_names):
        print(f"[INFO] Latest image {image_key} was already processed; no report sent.")
        return

    try:
        processed_image = process_image(image, aoi, satellite)
    except Exception as e:
        print(f"[ERROR] Processing image failed: {e}")
        sys.exit(1)

    failed_plots = asyncio.run(
        deliver_all(
            gdf,
            name_col,
            processed_image,
            date_str,
            satellite,
            image_key,
            connection,
        )
    )
    if failed_plots:
        print(f"[ERROR] Failed to process {len(failed_plots)} plot(s): {failed_plots}")
        sys.exit(1)
    print(f"[INFO] Pipeline complete. {len(gdf)} plots processed.")


def main():
    if not 0 <= CLOUD_SCORE_THRESHOLD <= 1:
        print("[ERROR] CLOUD_SCORE_THRESHOLD must be between 0 and 1.")
        sys.exit(1)

    connection = connect()
    try:
        run_pipeline(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
