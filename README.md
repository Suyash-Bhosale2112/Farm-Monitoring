# \U0001f33e Farm Monitoring Pipeline (V2)

An automated, high-resolution satellite imagery pipeline built on **Google Earth Engine (GEE)** and Python for continuous, near-real-time monitoring of agricultural farm plots. 

This pipeline automatically ingests imagery from multiple satellite constellations, harmonizes the data, applies cloud masking, resamples the imagery to a sharp 2-meter resolution, and calculates a suite of crucial vegetation indices before delivering heatmaps via Telegram.

---

## \u2728 Key Features

- **Multi-Satellite Workflow**: Maximizes cloud-free image availability by seamlessly querying data from **Sentinel-2**, **Landsat 8**, and **Landsat 9**.
- **High-Resolution (2m) Resampling**: Downscales native 10m/30m optical data to a 2-meter grid via bilinear interpolation, ensuring perfect, artifact-free vector boundaries for individual farm polygons.
- **Robust Cloud Masking**: Automatically interprets `SCL` (Sentinel) and `QA_PIXEL` (Landsat) bitmasks to strip out clouds, cirrus, and dilated cloud shadows.
- **Automated Vegetation Indices**: Calculates 6 critical agronomic indicators:
  - `NDVI` (Normalized Difference Vegetation Index)
  - `NDMI` (Normalized Difference Moisture Index)
  - `NDWI` (Normalized Difference Water Index)
  - `NDRE` (Normalized Difference Red Edge)
  - `EVI` (Enhanced Vegetation Index)
  - `GCI` (Green Chlorophyll Index)
- **Telegram Delivery**: Generates combined, aesthetically pleasing 2x3 grid visualizations (with legends and titles) and delivers them automatically to your Telegram via a Bot.
- **CI/CD Integrated**: Includes a GitHub Action workflow to automatically lint code on push.

---

## \U0001f4c2 Project Structure

```text
Farm-Monitoring-V1/
├── pipeline.py             # Main execution script and GEE processing logic
├── requirements.txt        # Python dependencies
├── README.md               # Project documentation
├── data/
│   └── farms.geojson       # Vector geometries of the farm plots to monitor
└── .github/
    └── workflows/          # GitHub Actions CI/CD configurations
```

---

## \u2699\ufe0f Prerequisites & Setup

1. **Python 3.9+** is required.
2. Install the necessary dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. **Google Earth Engine**: 
   - Ensure you have an active GEE account.
   - For server environments, set the `GEE_SERVICE_ACCOUNT_KEY` environment variable with your JSON key.
   - For local development, the script will automatically trigger a browser-based authentication fallback `ee.Authenticate()`.
4. **Telegram Integration** (Optional):
   Set the following environment variables to enable delivery:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

---

## \U0001f680 Usage

To execute the pipeline locally:
```bash
python pipeline.py
```

**How it works:**
1. The script reads your custom farm geometries from `data/farms.geojson`.
2. It queries GEE for the latest available clear image (under 20% cloud cover) from the past 14 days.
3. The image is masked, harmonized, and spatially resampled to 2 meters.
4. Vegetation indices are calculated server-side.
5. Thumbnails are fetched, stitched into a single PNG presentation, and printed/messaged to the user.

---

## \U0001f9ec Data Sources
- **COPERNICUS/S2_SR_HARMONIZED** (Sentinel-2 Surface Reflectance)
- **LANDSAT/LC08/C02/T1_L2** (Landsat 8 Surface Reflectance)
- **LANDSAT/LC09/C02/T1_L2** (Landsat 9 Surface Reflectance)
