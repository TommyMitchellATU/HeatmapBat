# Data Architecture – HeatmapBat

## Overview

The `/data` folder stores **bat detection data** in three stages:

1. **Raw input** – Detector summary files from field equipment (67 files in `Summary Files/`)
2. **Processed** – PostgreSQL database (865,609 detection samples from May-September 2024)
3. **Pre-computed analytics** – H3 hexagon Parquet files (resolution 10) for instant API responses (125 daily files, 512 KB)

---

## Folder Structure

### `MAUG-4050_A_Summary.txt` (436 KB)
**Purpose:** Legacy single detector file (retained for backward compatibility)
**Imported:** ✅ Yes, included in the 858,070 total rows

### `Summary Files/` (48 MB)
**Purpose:** Main source folder containing all detector summary data  
**Structure:**
```
Summary Files/
├── D01-GANN-3591_A_Summary.txt    ← Active files (67 total)
├── D01-GANN-3591_B_Summary.txt
├── MAUG-4050_A_Summary.txt
├── ...
├── MEEN-7103_A_Summary.txt
├── NA/                             ← EXCLUDED (2 files)
└── special/                        ← EXCLUDED (11 files)
```
**Imported:** ✅ 67 active files = **858,070 rows** into database  
**Excluded:** NA and special folders as requested

---

### `analytics/h3_daily/` (512 KB, 125 Parquet files)
**Purpose:** Pre-computed H3 hexagon aggregations by date (Resolution 10 - fine-grained)  
**Format:** Parquet (Apache columnar format, optimized for analytics)
**Contents:** One file per day spanning May 7 – October 9, 2024 (`h3_analytics_YYYY-MM-DD.parquet`)  
**Sample coverage:** All 865,609 detection samples across 125 days
**Resolution:** 10 (finer spatial detail, ~1-5 km2 per hexagon)
**Aggregations:** 7 total hexagon cells across the study area
**How it's used:** ⚡ **Served directly by API** for instant heatmap response
  - Frontend calls `/api/heatmap/h3_parquet`
  - Returns 7 detailed hexagons with accurate detection counts
  - No DB computation needed, sub-millisecond response
  
**Lifecycle:** 🔄 Generated periodically, NOT source control
```bash
# Generate from database at resolution 10
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data --skip-import --resolution 10
```

---

### `exports/` (1.2 MB)
**Purpose:** Optional sample exports for external use  
**Contents:** 
- `maug_points.geojson` – GeoJSON version of all points
- `maug_points_2024-05-16.csv` – Date-filtered CSV export

**How it's used:** Manual analysis or sharing data outside the platform  
**Lifecycle:** 🔄 Generated on-demand, NOT source control

---

## Data Pipeline

```
Field Equipment
     ↓
MAUG-4050_A_Summary.txt (Raw CSV)
     ↓  [cli_import]
PostgreSQL Database (maug_summary_samples table)
     ↓  [pipeline.py: H3 aggregation]
data/analytics/h3_daily/*.parquet
     ↓  [API reads on-demand]
Frontend Heatmap (instant response)
```

---

## Key Design Decisions

### ✅ Pre-computed H3 Hexagons (NOT real-time computation)

**Why?**
- H3 cell calculation is CPU-intensive (~0.1ms per point × 7,500+ points = 750ms+ per request)
- Pre-computing once saves repeatedly computing at runtime
- Map viewers typically need 10-50+ requests (zoom changes, pan, date range changes)

**Result:** ~100x faster API response times (1-5ms vs ~500-1000ms)

### ✅ Parquet Format (NOT CSV/JSON)

**Why?**
- Columnar storage compresses better (h3_daily files: ~3-4 KB each)
- Fast filtering by date/h3_index
- Pandas integration (easy to read/write)
- Standard in data pipelines

### ✅ One File Per Day (NOT single merged file)

**Why?**
- Incremental updates possible (add new day without recomputing all)
- Date filtering at the filesystem level
- Parallel processing for future scalability

---

## Storage Summary

| Item | Size | Keep? | Status |
|------|------|-------|--------|
| `MAUG-4050_A_Summary.txt` | 436 KB | ✅ YES | Imported as part of 858K rows |
| `Summary Files/` | 48 MB | ✅ YES | **67 active files, 858,070 rows imported** |
| `analytics/h3_daily/` | 512 KB | ✅ YES | **125 daily Parquet files (resolution 10, May-Oct 2024)** |
| `exports/` | 1.2 MB | ⚠️ OPTIONAL | Optional for manual sharing |
| `Summary Files/NA/` | N/A | ❌ NO | Intentionally excluded (2 files) |
| `Summary Files/special/` | N/A | ❌ NO | Intentionally excluded (11 files) |

**Database Stats:**
- **Total samples:** 865,609 detection records
- **Total detections:** 124,077 (sum of all files_count)
- **Date range:** May 7 – October 9, 2024 (125 days)
- **Source:** 67 detector files from Summary Files/ folder
- **Pre-computed hexagons:** 125 Parquet files at H3 resolution 10
- **Hexagon cells:** 7 total (finer spatial granularity)

---

## Common Operations

### Bulk import all detector files (excluding NA and special)
```bash
# Already completed: 67 files, 858,070 rows imported

# To import future files, use this Python script:
docker compose exec -T api uv run python << 'PYTHON'
from pathlib import Path
from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file

summary_dir = Path("/data/Summary Files")
db = SessionLocal()
total_rows = 0

try:
    files = sorted([f for f in summary_dir.glob("*_Summary.txt")])
    for file_path in files:
        rows = load_summary_file(db, file_path)
        total_rows += rows
        print(f"  {file_path.name:<45} {rows:6d} rows")
    print(f"Total: {total_rows:,} rows")
finally:
    db.close()
PYTHON
```

### Import new detector data
```bash
# Add new file to data/Summary Files/ folder (e.g., NEW_DETECTOR_Summary.txt)
docker compose exec api uv run python -m app.backend.eti.cli_import /data/MAUG-4051_B_Summary.txt

# Regenerate H3 analytics from updated database
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data --skip-import
```

### Export for external use
```bash
# CSV with date filter
docker compose exec api uv run python -m app.backend.eti.load.cli_export \
  --start "2024-05-16" --end "2024-05-20" \
  /data/exports/filtered_2024-05-16-to-20.csv

# GeoJSON for all data
docker compose exec api uv run python -m app.backend.eti.load.cli_geojson_export \
  /data/exports/all_points.geojson
```

### Check what's in the database
```bash
docker compose exec db psql -U app -d app -c \
  "SELECT DATE(timestamp_utc), COUNT(*), SUM(files_count) FROM maug_summary_samples GROUP BY DATE(timestamp_utc);"
```

---

## Git Strategy

The `.gitignore` file has been configured to:
- ✅ **Track:** `data/MAUG-4050_A_Summary.txt` (legacy source data)
- ✅ **Track:** `data/Summary Files/` (all 67 detector files + stub folders)
- ❌ **Ignore:** `analytics/h3_daily/*.parquet` (generated)
- ❌ **Ignore:** `exports/` (generated)
- ❌ **Ignore:** Docker volumes (`db-data/`, `minio-data/`)

To regenerate analytics after cloning:
```bash
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data --skip-import
```

This will:
1. Read all 865K samples from PostgreSQL (already imported)
2. Generate 125 Parquet files with H3 hexagon aggregations at resolution 10
3. Create 7 detailed hexagon cells across the study area
4. Save to `data/analytics/h3_daily/` (512 KB total)

---

## Future Enhancements

- [ ] S3 integration for cloud storage (pre-computed parquets in MinIO)
- [ ] Incremental H3 updates (only new dates)
- [ ] Parquet partitioning by month/year for faster queries
- [ ] Automated daily pipeline scheduling (Celery or APScheduler)
