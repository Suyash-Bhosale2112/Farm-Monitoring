# 🛰️ Automated Farm Vegetation Monitor — Version 1.0

> **Automated satellite monitoring for farm plots.**
> Every time Sentinel-2 satellite passes over your farm, this system automatically detects it, computes 6 vegetation & moisture indices entirely on Google Earth Engine's cloud, stitches professional heatmap images, and delivers them directly to your phone via Telegram — with no manual intervention whatsoever.

---

## 📌 Table of Contents
1. [What the System Does](#what-the-system-does)
2. [Vegetation Indices Explained](#vegetation-indices-explained)
3. [Project Structure](#project-structure)
4. [Architecture Overview](#architecture-overview)
5. [GitHub Setup — Step-by-Step](#github-setup--step-by-step)
6. [GitHub Secrets Configuration](#github-secrets-configuration)
7. [How the Automation Works](#how-the-automation-works)
8. [Understanding the Heatmap Images](#understanding-the-heatmap-images)
9. [Troubleshooting](#troubleshooting)
10. [Version History](#version-history)

---

## What the System Does

| Step | What Happens |
|------|-------------|
| **1. Daily trigger** | GitHub Actions wakes up at 06:00 UTC (11:30 IST) every morning |
| **2. Image detection** | Checks if a new Sentinel-2 satellite image exists over your farm in the last 48 hours |
| **3. No image?** | Script exits cleanly (exit code 0, no cost, no Telegram message) |
| **4. New image found** | Applies cloud mask (SCL bands), scales reflectance, clips to each farm plot boundary |
| **5. Index computation** | Computes NDVI, NDMI, NDWI, NDRE, EVI, GCI — entirely on GEE's servers |
| **6. PNG rendering** | Downloads rendered heatmap tiles (512×512 px each) per plot per index |
| **7. Image stitching** | Assembles a 1024×1606 px combined grid with title bar + colour legends using Pillow |
| **8. Telegram delivery** | Sends one image per farm plot to your Telegram chat via `@farmefefwebot` |

**Nothing is stored to disk. No GeoTIFFs, no Excel files, no databases.** All processing happens on GEE's cloud and all images are sent directly to Telegram in memory.

---

## Vegetation Indices Explained

| Index | Full Name | What It Measures | Colour Scale |
|-------|-----------|-----------------|--------------|
| **NDVI** | Normalized Difference Vegetation Index | Overall crop health & biomass | 🔴 Red (bare/stressed) → 🟡 Yellow → 🟢 Dark Green (dense) |
| **NDMI** | Normalized Difference Moisture Index | Canopy water content & irrigation status | 🔴 Dark Red (drought) → 🟡 Yellow → 🔵 Blue (waterlogged) |
| **NDWI** | Normalized Difference Water Index | Surface water and soil moisture | 🟫 Brown (dry soil) → ⬜ White → 🩵 Teal (open water) |
| **NDRE** | Normalized Difference Red Edge Index | Canopy chlorophyll (more sensitive than NDVI) | 🔴 Red (low) → 🟡 Yellow → 🟢 Dark Green (high canopy) |
| **EVI** | Enhanced Vegetation Index | Vegetation density with reduced atmosphere distortion | 🔴 Red (sparse) → 🟡 Yellow → 🟢 Dark Green (dense) |
| **GCI** | Green Chlorophyll Index | Leaf chlorophyll content (plant nutrition) | 🟡 Pale Yellow (low) → 🩵 Teal → 🔵 Deep Blue (high) |

### Reading the Images

Each panel has a **colour legend bar** at the bottom:
```
NDVI    -0.1 ████████████████████████████████ 0.9
         ^min                              max^
```
- Pixels **matching the LEFT colour** = values near `min` (stressed/bare)
- Pixels **matching the RIGHT colour** = values near `max` (dense/healthy)
- **Black areas** = cloud-masked pixels (data removed for accuracy)

---

## Project Structure

```
Farm-Monitoring-V1/            ← Upload this entire folder to GitHub
│
├── pipeline.py                ← Main automation script (all logic here)
├── requirements.txt           ← Python dependencies
├── README.md                  ← This file
├── .gitignore                 ← Prevents secret keys from being committed
│
├── data/
│   └── farms.geojson          ← Farm plot boundaries (Plot_name + geometry)
│
└── .github/
    └── workflows/
        └── daily_run.yml      ← GitHub Actions schedule definition
```

---

## Architecture Overview

```
Sentinel-2 Satellite  (new pass every ~5 days over Nashik region)
        │
        ▼
Google Earth Engine Cloud
        │  project: suyash-484207
        │  → Query Sentinel-2 SR Harmonized collection
        │  → Filter: last 48 h, <20% cloud cover
        │  → Apply SCL cloud mask (codes 3,8,9,10)
        │  → Compute 6 indices server-side
        │  → Render PNG tiles per plot per index via getThumbURL
        │
        ▼
GitHub Actions Runner (Ubuntu, free tier)
        │  runs pipeline.py at 06:00 UTC daily
        │  → Downloads only lightweight PNG bytes
        │  → Stitches 2×3 grid + title + legend using Pillow (in memory)
        │
        ▼
Telegram Bot API (@farmefefwebot)
        │
        ▼
Agronomist's Phone (Telegram chat)
```

---

## GitHub Setup — Step-by-Step

### Step 1: Create a GitHub Account
If you don't already have one, go to [github.com](https://github.com) and sign up for a free account.

---

### Step 2: Create a New Repository

1. Click the **`+`** icon at the top-right → **New repository**
2. Fill in the details:
   - **Repository name:** `farm-monitor`
   - **Visibility:** `Private` ✅ (recommended — keeps your farm data private)
   - **Do NOT** tick "Add a README file" (we already have one)
3. Click **Create repository**

---

### Step 3: Upload the Project Files

**Option A — Upload via GitHub Web (Easiest, no Git required):**

1. On your newly created empty repository page, click **Upload files**
2. Drag and drop the **entire `Farm-Monitoring-V1` folder contents** (all files and folders inside it) into the upload area
3. Important: Make sure the **`.github`** folder is included — it contains the automation workflow
4. In the "Commit changes" box, type: `Initial commit — Farm Monitor V1`
5. Click **Commit changes**

> ⚠️ **Windows Note:** Hidden folders starting with `.` (like `.github`) may not show in the file picker. Use the drag-and-drop method or enable "Show hidden items" in Windows Explorer (View → Hidden items).

**Option B — Using Git (for users comfortable with the terminal):**
```bash
cd path/to/Farm-Monitoring-V1
git init
git remote add origin https://github.com/YOUR_USERNAME/farm-monitor.git
git add .
git commit -m "Initial commit — Farm Monitor V1"
git branch -M main
git push -u origin main
```

---

### Step 4: Add GitHub Secrets

This is the **most important step.** Your service account key and Telegram credentials must never be stored in code — they live only as encrypted GitHub Secrets.

1. Go to your repository on GitHub
2. Click **Settings** (top tab bar)
3. In the left sidebar, click **Secrets and variables** → **Actions**
4. Click **New repository secret** for each of the 3 secrets below:

---

## GitHub Secrets Configuration

| Secret Name | Value to Paste | Where to Get It |
|-------------|---------------|----------------|
| `GEE_SERVICE_ACCOUNT_KEY` | The **entire contents** of `suyash-484207-803661abb1a7.json` (open it in Notepad, select all, copy) | Your Google Cloud Console → IAM → Service Accounts → Keys |
| `TELEGRAM_BOT_TOKEN` | `8717461461:AAG3fZCEs-CyGfYHZIu3lhrVvy7Dg2i7Xy0` | BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | `1411008960` | Your Telegram user ID (auto-detected from `/start` message) |

> ⚠️ **SECURITY WARNING:** Never commit `suyash-484207-803661abb1a7.json` to GitHub. The `.gitignore` file in this project prevents this by blocking all `*.json` files. The service account key must only exist as a GitHub Secret.

---

### Step 5: Verify the Workflow File is Present

After uploading, confirm your repository looks like this:

```
farm-monitor/
  ├── .github/workflows/daily_run.yml   ✅ must be here
  ├── data/farms.geojson                ✅ must be here
  ├── pipeline.py                       ✅ must be here
  ├── requirements.txt                  ✅ must be here
  └── README.md
```

If `.github/workflows/daily_run.yml` is missing, the automation will NOT run.

---

### Step 6: Test the Workflow Manually

1. Go to your repository → click the **Actions** tab
2. In the left sidebar, click **Daily Farm Monitor**
3. Click **Run workflow** → **Run workflow** (green button)
4. Watch the live logs appear — the job takes about 3–8 minutes
5. Check your **Telegram** — you should receive heatmap images for all plots!

If the job shows a green ✅ checkmark, everything is working. It will now run automatically every day at 11:30 IST.

---

## How the Automation Works

```
Every day at 11:30 IST (06:00 UTC):
    GitHub Actions wakes up
    └── Checks if new Sentinel-2 image exists in last 48 h
         ├── NO IMAGE → exit(0), no Telegram, zero cost
         └── NEW IMAGE FOUND
              ├── Cloud mask applied (SCL bands)
              ├── 6 indices computed on GEE servers
              ├── PNG tiles fetched for each plot × each index
              ├── Combined 1024×1606 px image assembled in memory
              └── Telegram message sent per plot
```

**Sentinel-2 revisit time:** approximately every 5 days over the Nashik region.
This means you receive images roughly 5–6 times per month whenever the sky is clear (less than 20% cloud cover).

---

## Understanding the Heatmap Images

Each Telegram message contains one image for one farm plot:

```
┌─────────────────────────────────┐  ← Title bar: "plot_1 • 2026-03-14"
│  NDVI panel  │  NDMI panel      │
│  512×512 px  │  512×512 px      │
├──────────────┼──────────────────┤
│  NDWI panel  │  NDRE panel      │
│              │                  │
├──────────────┼──────────────────┤
│  EVI panel   │  GCI panel       │
│              │                  │
└─────────────────────────────────┘
  each panel has index name + colour legend bar at bottom
```

**Image dimensions:** 1024 × 1606 pixels  
**Format:** PNG (lossless)  
**One image per plot, per satellite pass**

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Job fails: "GEE Authentication failed" | `GEE_SERVICE_ACCOUNT_KEY` secret not set or malformed | Re-paste the entire JSON content into the secret |
| Job fails: "Permission denied" | Service account missing Earth Engine roles | Add **Earth Engine Resource Writer** role in Google Cloud IAM |
| Job exits cleanly but no Telegram | No new satellite image in last 48 h | Wait for next satellite pass (~5 days cycle) |
| Job fails: "Telegram" errors | `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` wrong | Double-check the secret values match exactly |
| All panels are black | Heavy cloud cover over all plots | Cloud mask removed all pixels — wait for clear sky pass |
| Some panels black, others visible | Partial cloud cover | Normal — only affected plots are masked |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| **v1.0** | March 2026 | Initial release — 6 indices, Telegram delivery, GitHub Actions automation, industry-standard colour palettes with legends |

---

## Technical Specifications

| Attribute | Value |
|-----------|-------|
| Satellite | ESA Sentinel-2 SR Harmonized (10/20 m resolution) |
| GEE Project | `suyash-484207` |
| Service Account | `farm-monitor-home@suyash-484207.iam.gserviceaccount.com` |
| Cloud Cover Filter | < 20% per scene |
| Time Window | Last 48 hours (rolling) |
| Panel Resolution | 512 × 512 pixels |
| Final Image | 1024 × 1606 pixels PNG |
| Delivery | Telegram Bot `@farmefefwebot` |
| Schedule | Daily at 06:00 UTC (11:30 IST) |
| Runtime | ~3–8 minutes per run |
| Cost | Free (GEE non-commercial tier + GitHub Actions free tier) |

---

*Built with ❤️ for the Dhawale Farms, Nashik — Automated Farm Vegetation Monitor v1.0*
