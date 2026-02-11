# HeatmapBat

Interactive bat activity heatmap for Ireland. FastAPI backend serves bat detector summary data as H3 hexagon aggregates; a MapLibre-based UI provides timeline playback, zoom-adaptive hex resolution, and per-cell drill-down. Data can be sourced from PostGIS, local files, or S3/MinIO.

![HeatmapBat Screenshot](https://via.placeholder.com/800x400?text=HeatmapBat+Map+UI)

## Features

- **Interactive Map**: MapLibre GL map centered on Ireland with H3 hexagon visualization
- **Timeline Playback**: Day-by-day animation with sparkline activity graph, play/pause, speed controls
- **Cumulative Mode**: Toggle between single-day view and cumulative data up to selected date
- **Zoom-Adaptive Resolution**: H3 resolution adjusts automatically (res 3–12) as you zoom in/out
- **Click Details**: Popup showing detection counts, samples, and location info per hexagon
- **Flexible Data Sources**: Read from PostGIS, local filesystem, or MinIO/S3 with environment flags
- **Full ETL Pipeline**: Import → H3 analytics → Export → Upload to S3 in one command

## Stack

| Service | Port | Purpose |
|---------|------|---------|
| FastAPI | 8000 | API + static UI |
| PostGIS | 5432 | Sample storage (compose-internal) |
| MinIO S3 | 9000 | Object storage API |
| MinIO Console | 9001 | Web UI for S3 (minioadmin/minioadmin) |
| Redis | 6379 | Reserved for caching (not yet wired) |

## Quick Start

```bash
# Start all services
docker compose up -d --build

# Verify
curl http://localhost:8000/health

# Open UI
"$BROWSER" http://localhost:8000/
```

The API runs with hot-reload; edit files in `backend/` and changes apply immediately.

## Data Pipeline

### Adding New Data

1. Place detector summary files (`*_Summary.txt`) in the `data/` folder
2. Run the full pipeline:

```bash
docker compose exec api bash -c "
  uv run python -m app.backend.eti.cli_import /data && \
  uv run python -m app.backend.eti.pipeline && \
  uv run python -m app.backend.eti.upload_all_to_s3
"
```

This imports files → generates H3 analytics → uploads everything to MinIO.

### Pipeline Options

```bash
# Full pipeline with CSV/GeoJSON exports
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics --csv --geojson

# Skip import (use existing DB data)
docker compose exec api uv run python -m app.backend.eti.pipeline --skip-import

# Custom date range and resolution
docker compose exec api uv run python -m app.backend.eti.pipeline \
  --start 2024-05-16 --end 2024-06-01 --resolution 8
```

### Individual Steps

**1. Import Detector Summary Files**
```bash
docker compose exec api uv run python -m app.backend.eti.cli_import /data
```

**2. Generate H3 Analytics**
```bash
docker compose exec api uv run python -m app.backend.eti.pipeline --skip-import
```

**3. Upload to MinIO**
```bash
docker compose exec api uv run python -m app.backend.eti.upload_all_to_s3
```

**4. Export Data (optional)**
```bash
# CSV
docker compose exec api uv run python -m app.backend.eti.load.cli_export \
  --start "2024-05-16" --end "2024-05-17" /data/exports/points.csv

# GeoJSON
docker compose exec api uv run python -m app.backend.eti.load.cli_geojson_export \
  --start "2024-05-16" --end "2024-05-17" /data/exports/points.geojson
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health`, `GET /live` | Health/liveness probes |
| `GET /api/timeline/dates` | Available dates with per-day sample counts (drives timeline UI) |
| `GET /api/heatmap/points?start&end` | Raw sample points (lat, lon, timestamp, raw_count) |
| `GET /api/heatmap/h3?start&end&resolution=10` | Real-time H3 aggregation from DB |
| `GET /api/heatmap/h3_parquet?start&end` | Pre-computed H3 from Parquet files |
| `GET /` | MapLibre web UI |

## Data Source Configuration

Control where endpoints read data via environment variables in `.env`:

```bash
# Use MinIO for all endpoints
HEATMAP_SOURCE=s3

# Or per-endpoint
HEATMAP_POINTS_SOURCE=s3   # /api/heatmap/points
HEATMAP_H3_SOURCE=s3       # /api/heatmap/h3_parquet
```

| Variable | Values | Default |
|----------|--------|---------|
| `HEATMAP_SOURCE` | `db`, `local`, `s3` | — (shared fallback) |
| `HEATMAP_POINTS_SOURCE` | `db`, `local`, `s3` | `db` |
| `HEATMAP_H3_SOURCE` | `db`, `local`, `s3` | `local` |

Objects from S3 are cached in-memory for 5 minutes.

## MinIO / S3

Access the MinIO console at **http://localhost:9001** (login: `minioadmin` / `minioadmin`).

Bucket `heatmapbat` is auto-created on startup. Data structure:
```
heatmapbat/
├── data/exports/
│   ├── maug_points.geojson
│   └── maug_points_2024-05-16.csv
├── data/analytics/h3_daily/
│   ├── h3_analytics_2024-05-16.parquet
│   └── ...
└── data/maug_summary_samples_combined.csv
```

## Database Access

```bash
# SQL shell
docker compose exec db psql -U app -d app

# Quick query
docker compose exec db psql -U app -d app -c "SELECT COUNT(*) FROM maug_summary_samples;"

# Python access
docker compose exec api uv run python
>>> from app.backend.eti.db import SessionLocal
>>> from app.backend.eti.models import MaugSummarySample
>>> db = SessionLocal()
>>> db.query(MaugSummarySample).count()
```

## Tests and Quality

```bash
# All tests in container (matches CI)
docker compose exec api uv run pytest -q

# Local dev (from backend/)
cd backend
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -q
```

CI runs two jobs: lint/type/unit tests, and full Docker Compose integration.

## Repo Layout

```
.
├── docker-compose.yml          # PostGIS, Redis, MinIO, FastAPI
├── .env                        # Environment overrides (HEATMAP_SOURCE=s3)
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml          # Dependencies (uv)
│   ├── USAGE.md                # Complete command reference
│   └── app/
│       ├── main.py             # FastAPI app with all routes
│       ├── static/index.html   # MapLibre UI (timeline, hexagons)
│       └── backend/eti/
│           ├── db.py                 # SQLAlchemy session factory
│           ├── models.py             # MaugSummarySample ORM model
│           ├── s3.py                 # MinIO/S3 client helpers
│           ├── pipeline.py           # Full ETL orchestrator
│           ├── cli_import.py         # CLI: import summary files
│           ├── upload_all_to_s3.py   # CLI: upload all data to MinIO
│           ├── extract/              # Detector summary file parser
│           ├── transform/            # H3 analytics generation
│           └── load/                 # CSV/GeoJSON export CLIs
├── data/
│   ├── *_Summary.txt           # Detector summary files
│   ├── exports/                # CSV/GeoJSON outputs
│   └── analytics/h3_daily/     # Pre-computed H3 Parquet
└── db/init.sql                 # PostGIS + table schema
```

## Documentation

See [backend/USAGE.md](backend/USAGE.md) for the complete command reference including:
- All import/export commands
- SQL queries and Python database access
- Pipeline automation options
- Environment variables
- Troubleshooting
