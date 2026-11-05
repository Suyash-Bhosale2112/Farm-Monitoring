# Farm Monitoring Pipeline

A student project for monitoring farm plots with Google Earth Engine (GEE). It
selects the latest suitable satellite image, applies cloud masking, calculates
vegetation indices, saves plot reports and measurements to SQLite, and offers a
Streamlit dashboard for reviewing history and possible NDVI drops.

## Features

- Sentinel-2 Surface Reflectance and Landsat 8/9 imagery from GEE.
- Sentinel-2 Cloud Score+ masking, configurable with `CLOUD_SCORE_THRESHOLD`
  (default `0.60`, valid range `0` to `1`). Landsat uses its `QA_PIXEL` cloud,
  cirrus, shadow, and snow flags.
- 5 m output grid for processing and visualisation. Resampling does not add
  spatial detail beyond the source image: Sentinel-2 is natively 10 m or
  coarser for some bands, and Landsat is natively 30 m.
- NDVI, NDMI, NDWI, EVI, and GCI for Sentinel-2 and Landsat; NDRE is calculated
  and shown only for Sentinel-2, which provides a red-edge band.
- One combined heatmap report per plot and acquisition, plus per-plot index
  measurements and satellite/date metadata in SQLite.
- Duplicate prevention: completed plot/image pairs are recorded and the latest
  already-completed image is not reported again. Failed plot reports are not
  recorded as complete, so they can be retried.
- Optional Telegram delivery and a dashboard for index trends, plot comparison,
  latest heatmaps, delivery status, and configurable NDVI-drop review signals.
- GitHub Actions for tests and scheduled processing.

## Requirements and setup

Use Python 3.10 or later.

```bash
python -m pip install -r requirements.txt
```

The pipeline requires a Google Earth Engine account and project. For local
development, the first run can prompt for browser authentication. For GitHub
Actions, add these repository Actions secrets:

- `GEE_SERVICE_ACCOUNT_KEY`: service account JSON key as a single JSON string.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: optional; omit both to save
  reports without Telegram delivery.
- `CLOUD_SCORE_THRESHOLD`: optional repository Actions variable, from `0` to
  `1`; defaults to `0.60`.

The project ID in the service account key is used by the pipeline when
initialising GEE.

Run the pipeline from any working directory:

```bash
python pipeline.py
```

`CLOUD_SCORE_THRESHOLD` can be set in the environment to tune Sentinel-2
Cloud Score+ masking. Higher values are stricter and may mask more pixels. For
example, in PowerShell:

```powershell
$env:CLOUD_SCORE_THRESHOLD = "0.65"
python pipeline.py
```

The acquisition search covers the most recent 14 days and uses the latest
available scene with a scene-level cloud-cover value under 30%. A cloud score
is a pixel-level mask, not a guarantee that every plot will have enough clear
pixels for a useful report.

## Saved results

By default, the pipeline writes:

- `data/farm_monitoring.db` — SQLite observations for each plot and image.
- `data/reports/` — combined PNG heatmaps referenced by the database.

The database has one row for each successfully rendered plot/image pair. It
stores the acquisition date and satellite, mean available index values, report
path, and Telegram delivery status. Landsat rows leave `ndre` empty. Failed
heatmap panels fail the entire plot report instead of being replaced with a
misleading placeholder; an unrecorded report can be retried while that image
remains the latest available scene.

The scheduled GitHub Actions workflow commits changes to the database and
reports back to the repository so subsequent daily runs and the dashboard can
reuse the history. It needs repository **Contents: write** permission. If you
run the pipeline elsewhere, keep the database and reports together and copy
them to the same machine/environment where the dashboard runs.

Override the storage locations with `FARM_MONITOR_DB` and `FARM_REPORT_DIR`.
Relative paths are resolved from the repository folder.

## Dashboard

Start the local dashboard after at least one successful pipeline run:

```bash
python -m streamlit run dashboard.py
```

Select a plot to view its index history and latest heatmap. The comparison
section charts NDVI for selected plots. The alert threshold and baseline period
are adjustable in the sidebar. A review signal is raised when the latest NDVI
is below the mean of at least three earlier observations within the selected
baseline period by the configured absolute amount. It is an indicator to
investigate, not a crop diagnosis.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Data sources and interpretation

- `COPERNICUS/S2_SR_HARMONIZED` and
  `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED`
- `LANDSAT/LC08/C02/T1_L2`
- `LANDSAT/LC09/C02/T1_L2`

Index values are satellite-derived indicators and can vary with season,
atmosphere, crop type, and image availability. They should not be treated as a
standalone diagnosis of crop health.

Farm boundaries are read from [`data/farms.geojson`](./data/farms.geojson).
