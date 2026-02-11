# HeatmapBat – Complete Usage Guide

## Table of Contents
- [Quick Start](#quick-start)
- [Data Import](#data-import)
- [Data Export](#data-export)
- [MinIO/S3 Storage](#minios3-storage)
- [API Endpoints](#api-endpoints)
- [Database Operations](#database-operations)
- [Pipeline Automation](#pipeline-automation)
- [Development](#development)
- [Testing](#testing)

---

## Quick Start

```bash
# Start all services (API, PostgreSQL, Redis, MinIO)
cd /workspaces/HeatmapBat
docker compose up -d --build

# Verify health
curl http://localhost:8000/health

# Open the map UI
"$BROWSER" http://localhost:8000/

# View logs
docker compose logs -f api

# Stop everything
docker compose down
```

---

## Data Import

### Import a single detector file
```bash
docker compose exec api uv run python -m app.backend.eti.cli_import /data/MAUG-4050_A_Summary.txt
```

### Import all detector files in a directory
```bash
docker compose exec api uv run python -m app.backend.eti.cli_import /data
```

### Import with Python script (more control)
```bash
docker compose exec -T api uv run python - << 'PY'
from pathlib import Path
from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file

db = SessionLocal()
try:
    for path in Path("/data").glob("*_Summary.txt"):
        count = load_summary_file(db, path)
        print(f"✓ {path.name}: {count} rows")
finally:
    db.close()
PY
```

### Expected file format
Detector summary files (`*_Summary.txt`) should have this structure:
```
Date,Time,Latitude,Longitude,FilesCount,...
2024-05-16,20:55:59,51.7443,-9.31424,5,...
2024-05-16,21:00:00,51.7443,-9.31424,3,...
```

---

## Data Export

### Export to CSV (with date filter)
```bash
docker compose exec api uv run python -m app.backend.eti.load.cli_export \
  --start "2024-05-16" --end "2024-05-17" \
  /data/exports/points_2024-05-16.csv
```

### Export to GeoJSON (with date filter)
```bash
docker compose exec api uv run python -m app.backend.eti.load.cli_geojson_export \
  --start "2024-05-16" --end "2024-05-17" \
  /data/exports/points_2024-05-16.geojson
```

### Export all data to CSV (no filter)
```bash
docker compose exec api uv run python -m app.backend.eti.load.cli_export \
  /data/exports/all_points.csv
```

### Export directly from PostgreSQL
```bash
docker compose exec db psql -U app -d app -c \
  "COPY maug_summary_samples TO STDOUT WITH CSV HEADER" > data/export.csv
```

### Generate H3 Analytics (Parquet files)
```bash
docker compose exec api uv run python -m app.backend.eti.pipeline --skip-import
```
This creates `data/analytics/h3_daily/h3_analytics_YYYY-MM-DD.parquet` files.

---

## MinIO/S3 Storage

### Access MinIO Console
- URL: http://localhost:9001
- Username: `minioadmin`
- Password: `minioadmin`

### Upload all data to MinIO
```bash
docker compose exec api uv run python -m app.backend.eti.upload_all_to_s3
```
This uploads:
- Exports (GeoJSON, CSV) → `data/exports/`
- H3 analytics (Parquet) → `data/analytics/h3_daily/`
- Combined CSV → `data/maug_summary_samples_combined.csv`

### Upload a single CSV to MinIO
```bash
docker compose exec api uv run python -m app.backend.eti.export_to_s3
```

### Enable S3 mode (serve from MinIO instead of local files)
Add to `.env`:
```bash
HEATMAP_SOURCE=s3
```
Or per-endpoint:
```bash
HEATMAP_POINTS_SOURCE=s3   # /api/heatmap/points
HEATMAP_H3_SOURCE=s3       # /api/heatmap/h3_parquet
```

### Restart API to apply changes
```bash
docker compose restart api
```

---

## API Endpoints

### Health checks
```bash
curl http://localhost:8000/health   # Readiness probe
curl http://localhost:8000/live     # Liveness probe
```

### Get timeline dates (for slider)
```bash
curl http://localhost:8000/api/timeline/dates
```
Returns: `{dates: [{date, sample_count, total_detections}], min_date, max_date}`

### Get raw heatmap points
```bash
# All points
curl "http://localhost:8000/api/heatmap/points"

# Filtered by date
curl "http://localhost:8000/api/heatmap/points?start=2024-05-16T00:00:00&end=2024-05-17T00:00:00"

# From S3 GeoJSON
curl "http://localhost:8000/api/heatmap/points?points_object=data/exports/maug_points.geojson"
```

### Get H3 hexagon aggregates (real-time from DB)
```bash
# Default resolution 7
curl "http://localhost:8000/api/heatmap/h3"

# Custom resolution (0-15, higher = smaller hexes)
curl "http://localhost:8000/api/heatmap/h3?resolution=10"

# With date filter
curl "http://localhost:8000/api/heatmap/h3?start=2024-05-16T00:00:00&end=2024-05-17T00:00:00&resolution=8"
```

### Get H3 from pre-computed Parquet
```bash
# All analytics
curl "http://localhost:8000/api/heatmap/h3_parquet"

# Filtered by date
curl "http://localhost:8000/api/heatmap/h3_parquet?start=2024-05-16&end=2024-05-20"

# Custom analytics directory
curl "http://localhost:8000/api/heatmap/h3_parquet?analytics_dir=data/analytics/h3_daily"
```

---

## Database Operations

### Open SQL shell
```bash
docker compose exec db psql -U app -d app
```

#### Common SQL queries
```sql
-- Count all records
SELECT COUNT(*) FROM maug_summary_samples;

-- Records by date
SELECT DATE(timestamp_utc) as date, COUNT(*), SUM(files_count) as detections
FROM maug_summary_samples
GROUP BY DATE(timestamp_utc)
ORDER BY date;

-- Top locations by activity
SELECT lat, lon, SUM(files_count) as total
FROM maug_summary_samples
GROUP BY lat, lon
ORDER BY total DESC
LIMIT 10;

-- Date range
SELECT * FROM maug_summary_samples
WHERE timestamp_utc >= '2024-05-16' AND timestamp_utc < '2024-05-17';

-- Delete all data (reset)
TRUNCATE maug_summary_samples;
```

### Python database access
```bash
docker compose exec api uv run python
```
```python
from app.backend.eti.db import SessionLocal
from app.backend.eti.models import MaugSummarySample
from sqlalchemy import func

db = SessionLocal()

# Count records
print(db.query(func.count(MaugSummarySample.id)).scalar())

# Get first 5 records
for row in db.query(MaugSummarySample).limit(5):
    print(f"{row.timestamp_utc}: {row.lat}, {row.lon} - {row.files_count}")

# Sum by date
from sqlalchemy import cast, Date
results = db.query(
    cast(MaugSummarySample.timestamp_utc, Date).label('date'),
    func.sum(MaugSummarySample.files_count).label('total')
).group_by('date').all()

for r in results:
    print(f"{r.date}: {r.total}")

db.close()
```

---

## Pipeline Automation

### Full pipeline (import → analytics → upload)
```bash
docker compose exec api bash -c "
  uv run python -m app.backend.eti.cli_import /data && \
  uv run python -m app.backend.eti.pipeline && \
  uv run python -m app.backend.eti.upload_all_to_s3
"
```

### Pipeline with exports
```bash
docker compose exec api uv run python -m app.backend.eti.pipeline \
  /data /data/analytics --csv --geojson
```

### Pipeline options
```bash
# Skip import (reuse existing DB data)
docker compose exec api uv run python -m app.backend.eti.pipeline --skip-import

# Skip H3 analytics generation
docker compose exec api uv run python -m app.backend.eti.pipeline --skip-h3

# Custom date range
docker compose exec api uv run python -m app.backend.eti.pipeline \
  --start 2024-05-16 --end 2024-06-01

# Custom H3 resolution
docker compose exec api uv run python -m app.backend.eti.pipeline --resolution 8
```

### Add new data (incremental update)
```bash
# 1. Add new detector file to data/ folder
# 2. Run pipeline
docker compose exec api bash -c "
  uv run python -m app.backend.eti.cli_import /data && \
  uv run python -m app.backend.eti.pipeline && \
  uv run python -m app.backend.eti.upload_all_to_s3
"
```

---

## Development

### Run backend locally (outside Docker)
```bash
cd /workspaces/HeatmapBat/backend
uv sync
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000
```

### Lint and format
```bash
cd /workspaces/HeatmapBat/backend
uv run ruff check .           # Lint
uv run ruff format --check .  # Format check
uv run ruff format .          # Auto-format
uv run mypy .                 # Type check
```

### Rebuild containers
```bash
docker compose build --no-cache api
docker compose up -d
```

---

## Testing

### Run all tests (in container)
```bash
docker compose exec api uv run pytest -q
```

### Run all tests (locally)
```bash
cd /workspaces/HeatmapBat/backend
uv run pytest -q
```

### Run specific test file
```bash
docker compose exec api uv run pytest app/backend/tests/test_health.py -v
```

### Run with coverage
```bash
docker compose exec api uv run pytest --cov=app --cov-report=term-missing
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+psycopg://app:app@db:5432/app` | PostgreSQL connection |
| `S3_ENDPOINT_URL` | `http://minio:9000` | MinIO/S3 endpoint |
| `S3_BUCKET` | `heatmapbat` | S3 bucket name |
| `HEATMAP_SOURCE` | (none) | Shared data source: `db`, `local`, `s3` |
| `HEATMAP_POINTS_SOURCE` | `db` | Points endpoint source |
| `HEATMAP_H3_SOURCE` | `local` | H3 parquet endpoint source |

---

## Troubleshooting

### Container not starting
```bash
docker compose logs api
docker compose ps
```

### Database connection issues
```bash
docker compose exec db pg_isready -U app
```

### Reset everything
```bash
docker compose down -v  # Removes volumes (data loss!)
docker compose up -d --build
```

### Check MinIO bucket contents
```bash
# Via web console
"$BROWSER" http://localhost:9001

# Or via API
curl -s http://localhost:9000/heatmapbat
```