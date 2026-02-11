## HeatmapBat

Interactive bat activity heatmap for Ireland. FastAPI backend serves bat detector summary data as H3 hexagon aggregates; a MapLibre-based UI provides timeline playback, zoom-adaptive hex resolution, and per-cell drill-down. Data can be sourced from PostGIS, local files, or S3/MinIO.

## Features

- **Interactive Map**: MapLibre GL map centered on Ireland with H3 hexagon visualization
- **Timeline Playback**: Day-by-day animation with sparkline activity graph, play/pause, speed controls
- **Cumulative Mode**: Toggle between single-day view and cumulative data up to selected date
- **Zoom-Adaptive Resolution**: H3 resolution adjusts automatically (res 4–10) as you zoom in/out
- **Click Details**: Popup showing detection counts, samples, and location info per hexagon
- **Flexible Data Sources**: Read from PostGIS, local filesystem, or MinIO/S3 with environment flags

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

### Full Pipeline (Recommended)

Run the complete ETL in one command — imports files, generates H3 analytics, and optionally exports:

```bash
# Import + H3 analytics
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics

# With exports
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics --csv --geojson

# Skip import (use existing DB data)
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics --skip-import

# Date-filtered
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics \
  --start 2024-05-16 --end 2024-05-17 --resolution 7
```

### Individual Steps

For finer control, run each step separately:

**1. Import Detector Summary Files**

```bash
# Single file
docker compose exec api uv run python -m app.backend.eti.cli_import /data/D01-BAT-1397_A_Summary.txt
```

**2. Generate H3 Analytics**

```bash
docker compose exec api uv run python -m app.backend.eti.transform.cli_h3_analytics \
  --start "2024-05-16" --end "2024-05-17" --resolution 7 /data/analytics/h3_daily
```

**3. Export Data**

```bash
# CSV export
docker compose exec api uv run python -m app.backend.eti.load.cli_export \
  --start "2024-05-16" --end "2024-05-17" /data/exports/maug_points_2024-05-16.csv

# GeoJSON export
docker compose exec api uv run python -m app.backend.eti.load.cli_geojson_export \
  --start "2024-05-16" --end "2024-05-17" /data/exports/maug_points.geojson
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health`, `GET /live` | Health/liveness probes |
| `GET /api/timeline/dates` | Available dates with per-day sample counts (drives timeline UI) |
| `GET /api/heatmap/points?start&end` | Raw sample points (lat, lon, timestamp, raw_count) |
| `GET /api/heatmap/h3?start&end&resolution=7` | Server-side H3 aggregation from DB |
| `GET /api/heatmap/h3_parquet?start&end` | Pre-computed H3 Parquet files |
| `GET /` | MapLibre web UI |

## Data Source Flags

Control where endpoints read data via environment variables:

| Variable | Values | Default |
|----------|--------|---------|
| `HEATMAP_SOURCE` | `db`, `local`, `s3` | — (shared fallback) |
| `HEATMAP_POINTS_SOURCE` | `db`, `local`, `s3` | `db` |
| `HEATMAP_H3_SOURCE` | `db`, `local`, `s3` | `local` |

Set in `.env` at repo root or pass directly to compose. When `s3` is active, objects are cached in-memory for 5 minutes.

## MinIO / S3 Usage

Bucket `heatmapbat` is auto-created on startup when S3 mode is enabled.

```bash
# Upload via AWS CLI
export AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin
aws --endpoint-url http://localhost:9000 s3 cp data/exports/maug_points.geojson s3://heatmapbat/data/exports/

# Or use MinIO console at http://localhost:9001
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
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml          # Dependencies (uv)
│   └── app/
│       ├── main.py             # FastAPI app with all routes
│       ├── static/index.html   # MapLibre UI (timeline, hexagons)
│       └── backend/
│           ├── eti/
│           │   ├── db.py                 # SQLAlchemy session factory
│           │   ├── models.py             # MaugSummarySample ORM model
│           │   ├── s3.py                 # MinIO/S3 client helpers
│           │   ├── cli_import.py         # CLI: import summary files
│           │   ├── extract/              # Detector summary file parser
│           │   ├── transform/            # H3 analytics generation
│           │   └── load/                 # CSV/GeoJSON export CLIs
│           └── tests/                    # pytest suite
├── data/
│   ├── D*_Summary.txt          # Sample bat detector files
│   ├── exports/                # CSV/GeoJSON outputs
│   └── analytics/h3_daily/     # Pre-computed H3 Parquet
└── db/init.sql                 # PostGIS + table schema
```

## Limited Use Files

- `backend/app/backend/eti/export_to_s3.py` — Manual upload helper (requires pre-existing CSV; prefer using the pipeline with `--csv` then uploading via MinIO console or AWS CLI)
