# HeatmapBat

Interactive bat activity heatmap for Ireland. FastAPI backend serves bat detector summary data as H3 hexagon aggregates; a MapLibre-based UI provides timeline playback, zoom-adaptive hex resolution, and per-cell drill-down. Data can be sourced from PostGIS, local files, or S3/MinIO.

## Features

- **Interactive Map**: MapLibre GL map centered on Ireland with OpenStreetMap tiles and H3 hexagon visualization
- **Timeline Playback**: Day-by-day animation with sparkline activity graph, play/pause, speed controls (0.5x–4x)
- **Cumulative Mode**: Toggle between single-day view and cumulative data up to the selected date
- **Zoom-Adaptive Resolution**: H3 resolution adjusts automatically (res 3–12) as you zoom; client-side re-aggregation via h3-js
- **Click Details**: Popup showing detection counts, sample count, activity level, average per sample, and location info per hexagon
- **Colour-Coded Legend**: Five-tier activity scale from Minimal (<1,000) to Very High (50,000+)
- **Flexible Data Sources**: Read from PostGIS, local filesystem, or MinIO/S3 with environment flags
- **Full ETL Pipeline**: Import → H3 analytics → CSV/GeoJSON export → Upload to S3
- **In-Memory S3 Cache**: 5-minute TTL cache for S3 objects reduces latency and API calls

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

## Project Functions

### API Endpoints (`backend/app/main.py`)

All routes are defined in a single FastAPI application.

#### Operations

| Function | Route | Description |
|----------|-------|-------------|
| `health()` | `GET /health` | Readiness probe — returns `{"status": "ok"}` |
| `live()` | `GET /live` | Liveness probe — returns `{"status": "ok"}` |

#### Heatmap

| Function | Route | Description |
|----------|-------|-------------|
| `get_heatmap_points()` | `GET /api/heatmap/points` | Raw sample points (lat, lon, timestamp, raw_count). Supports `?start=&end=&points_object=`. Reads from DB, S3 GeoJSON, or S3 CSV depending on `HEATMAP_POINTS_SOURCE`. |
| `get_heatmap_h3()` | `GET /api/heatmap/h3` | Real-time H3 aggregation from PostGIS. Supports `?start=&end=&resolution=7` (range 0–15). Bins samples into hexagons on every request. |
| `get_heatmap_h3_parquet()` | `GET /api/heatmap/h3_parquet` | Pre-computed H3 from Parquet files. Supports `?start=&end=&analytics_dir=`. Reads from local filesystem or S3 depending on `HEATMAP_H3_SOURCE`. Includes hex polygon boundaries. |

#### Timeline

| Function | Route | Description |
|----------|-------|-------------|
| `get_timeline_dates()` | `GET /api/timeline/dates` | Per-day sample counts and total detections. Returns `{dates, min_date, max_date}` for the timeline slider and sparkline. |

#### UI

| Function | Route | Description |
|----------|-------|-------------|
| `index()` | `GET /` | Serves `index.html` — the MapLibre-based single-page app |

#### Internal Helpers

| Function | Purpose |
|----------|---------|
| `_resolve_source(point_var, hex_var)` | Determines data sources (db/local/s3) from environment variables with per-endpoint → shared → default precedence |
| `_get_cached_bytes(key)` | Retrieves S3 object bytes from in-memory cache if not expired (5-min TTL) |
| `_set_cached_bytes(key, data)` | Stores S3 object bytes in cache with current timestamp |
| `_ensure_bucket_on_startup()` | Startup event — creates the S3 bucket if S3 mode is enabled |

### Pydantic Response Models (`backend/app/main.py`)

| Model | Used By | Fields |
|-------|---------|--------|
| `HealthResponse` | `/health`, `/live` | `status` |
| `HeatmapPoint` | `/api/heatmap/points` | `lat`, `lon`, `raw_count`, `effort_normalised_weight`, `timestamp_utc` |
| `H3Cell` | `/api/heatmap/h3` | `h3_index`, `lat`, `lon`, `raw_count`, `effort_normalised_weight` |
| `H3ParquetCell` | `/api/heatmap/h3_parquet` | `h3_index`, `lat`, `lon`, `raw_count_sum`, `sample_count`, `detector_nights`, `detections_per_night`, `polygon` |
| `TimelineDateEntry` | `/api/timeline/dates` | `date`, `sample_count`, `total_detections` |
| `TimelineResponse` | `/api/timeline/dates` | `dates`, `min_date`, `max_date` |

### ORM Model (`backend/app/backend/eti/models.py`)

| Model | Table | Fields |
|-------|-------|--------|
| `MaugSummarySample` | `maug_summary_samples` | `id`, `site_id`, `timestamp_utc`, `lat`, `lon`, `power_v`, `temp_c`, `files_count`, `scrubbed_count`, `mic0_type`, `raw_date`, `raw_time` |

### Database (`backend/app/backend/eti/db.py`)

| Symbol | Purpose |
|--------|---------|
| `DATABASE_URL` | Connection string from `DATABASE_URL` env var (default: PostGIS via compose) |
| `engine` | Process-wide SQLAlchemy engine |
| `SessionLocal` | Session factory bound to the shared engine |
| `get_db()` | Generator yielding a scoped `Session` — used as `Depends(get_db)` in FastAPI |

### S3 / MinIO Helpers (`backend/app/backend/eti/s3.py`)

| Function | Purpose |
|----------|---------|
| `get_s3_client()` | Creates a boto3 S3 client using `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` env vars |
| `get_bucket_name()` | Returns the target bucket from `S3_BUCKET` env var (default: `heatmapbat`) |
| `ensure_bucket_exists()` | Creates the S3 bucket if it doesn't exist — safe to call repeatedly |
| `list_keys(prefix)` | Yields object keys under a prefix with pagination |
| `upload_fileobj(obj, key)` | Uploads a binary file-like object to S3 |
| `download_fileobj(key, obj)` | Downloads an S3 object into a file-like object |
| `get_object_bytes(key)` | Fetches an object and returns its raw bytes |

### ETL Pipeline (`backend/app/backend/eti/pipeline.py`)

| Symbol | Purpose |
|--------|---------|
| `PipelineResult` | Dataclass summarising a run: `files_imported`, `rows_imported`, `h3_output_dir`, `csv_path`, `geojson_path` |
| `run_etl(input_dir, output_dir, ...)` | Orchestrates Extract → Transform → Load. Supports `start`, `end`, `h3_resolution`, `export_csv`, `export_geojson`, `skip_import`, `skip_h3` flags. |
| `main()` | CLI entrypoint with argparse — `python -m app.backend.eti.pipeline` |

### Extract — Summary File Parser (`backend/app/backend/eti/extract/summary_import.py`)

| Function | Purpose |
|----------|---------|
| `parse_lat_lon(lat_str, ns_str, lon_str, ew_str)` | Converts raw LAT/NS/LON/EW strings to `(float, float)` with hemisphere sign |
| `parse_timestamp(date_str, time_str)` | Combines DATE (`2024-May-16`) and TIME (`20:55:59`) into a `datetime` |
| `parse_summary_file(path)` | Parses a `*_Summary.txt` CSV into a list of `MaugSummarySample` objects |
| `load_summary_file(db, path)` | Parses and inserts all rows from a summary file in a single DB transaction; returns row count |

### Transform — H3 Analytics (`backend/app/backend/eti/transform/h3_analytics.py`)

| Symbol | Purpose |
|--------|---------|
| `H3AnalyticsConfig` | Dataclass with `resolution` (default 7) and `time_freq` (default `"1D"`) |
| `run_h3_analytics(start, end, output_dir, config)` | Reads samples from DB, bins into H3 × time buckets, writes partitioned Parquet (`h3_analytics_YYYY-MM-DD.parquet`) |

Internal helpers:

| Function | Purpose |
|----------|---------|
| `_samples_to_dataframe(samples)` | Converts ORM rows to a pandas DataFrame with `timestamp_utc`, `lat`, `lon`, `raw_count`, `site_id` |
| `_attach_h3_and_time_bins(df, config)` | Adds `h3_index` and `time_bin_start` columns |
| `_aggregate(df)` | Two-stage aggregation: first per (time_bin, h3_index, site_id), then rolled up per (time_bin, h3_index) producing `raw_count_sum`, `sample_count`, `detector_nights`, `unique_sites`, and `detections_per_night` (effort-normalised) |
| `_write_partitioned_parquet(df, output_dir)` | Outputs one Parquet file per date |

### Load — Export Functions

#### CSV Export (`backend/app/backend/eti/load/export.py`)

| Function | Purpose |
|----------|---------|
| `export_samples_to_csv(db, out_path, start, end)` | Exports `maug_summary_samples` rows to CSV; returns row count |

#### GeoJSON Export (`backend/app/backend/eti/load/geojson_export.py`)

| Function | Purpose |
|----------|---------|
| `export_samples_to_geojson(db, out_path, start, end)` | Exports samples as a GeoJSON FeatureCollection with Point geometries and full properties; returns feature count |

#### S3 Upload (`backend/app/backend/eti/upload_all_to_s3.py`)

| Function | Purpose |
|----------|---------|
| `upload_file(local_path, s3_key)` | Uploads a single file to S3 |
| `main()` | Uploads all exports (GeoJSON/CSV), H3 Parquet analytics, and combined CSV to the MinIO bucket |

#### Legacy S3 Upload (`backend/app/backend/eti/export_to_s3.py`)

| Function | Purpose |
|----------|---------|
| `upload_csv(path, key)` | Uploads a local CSV file to S3 |
| `main()` | Uploads the combined detector summary CSV to S3 |

### CLI Entrypoints

| Module | Command | Description |
|--------|---------|-------------|
| `app.backend.eti.cli_import` | `python -m app.backend.eti.cli_import <path>` | Import a single summary file or directory of `*_Summary.txt` files into the database |
| `app.backend.eti.pipeline` | `python -m app.backend.eti.pipeline <input_dir> <output_dir> [options]` | Full ETL: import → H3 analytics → optional CSV/GeoJSON export |
| `app.backend.eti.upload_all_to_s3` | `python -m app.backend.eti.upload_all_to_s3` | Upload all exports + analytics to MinIO |
| `app.backend.eti.export_to_s3` | `python -m app.backend.eti.export_to_s3` | Upload the combined summary CSV to S3 |
| `app.backend.eti.transform.cli_h3_analytics` | `python -m app.backend.eti.transform.cli_h3_analytics <output_dir> [--start] [--end] [--resolution] [--time-freq]` | Run H3 × time analytics independently |
| `app.backend.eti.load.cli_export` | `python -m app.backend.eti.load.cli_export [--start] [--end] <out_path>` | Export samples to CSV |
| `app.backend.eti.load.cli_geojson_export` | `python -m app.backend.eti.load.cli_geojson_export [--start] [--end] <out_path>` | Export samples to GeoJSON |

### Frontend (`backend/app/static/index.html`)

Single-page MapLibre app with client-side H3 re-aggregation.

#### Key JavaScript Functions

| Function | Purpose |
|----------|---------|
| `initTimeline()` | Fetches `/api/timeline/dates`, configures slider and sparkline, loads initial data |
| `fetchDataForDate(dateStr, endDateStr)` | Fetches `/api/heatmap/h3_parquet` for a date range with client-side caching |
| `goToDate(index)` | Navigates timeline to a specific date index, fetches data, triggers render |
| `play()` / `pause()` | Start/stop automatic day-by-day playback with configurable speed |
| `buildSparkline()` | Renders per-day activity bars in the timeline; bars are clickable |
| `updateSparklineHighlight()` | Highlights current date and cumulative range in sparkline |
| `getH3ResolutionForZoom(zoom)` | Maps MapLibre zoom level to H3 resolution (zoom 5→res 3 up to zoom 15→res 12) |
| `aggregateToResolution(data, targetRes)` | Re-bins H3 cells to a coarser resolution client-side using `h3.cellToParent()` |
| `toHexGeoJSON(data)` | Converts H3 cell array to a GeoJSON FeatureCollection with Polygon geometries |
| `updateHexagonsForZoom()` | Called on `zoomend` — re-aggregates and re-renders hexagons at the new resolution |
| `renderHexagons(data)` | Adds/updates the MapLibre fill + line layers for hexagon rendering |
| `showPopup(lngLat, props)` | Displays a rich popup with detection count, sample count, activity level, and location |

### Test Suite (`backend/app/backend/tests/`)

| Test File | Coverage |
|-----------|----------|
| `test_health.py` | `/health` and `/live` endpoints return 200 |
| `test_timeline.py` | `/api/timeline/dates` response structure |
| `test_heatmap_points_s3.py` | `/api/heatmap/points` in S3 mode |
| `test_h3_parquet_s3.py` | `/api/heatmap/h3_parquet` in S3 mode |
| `test_heatmap_minio_shared_flag.py` | `HEATMAP_SOURCE=s3` shared flag behaviour |
| `test_h3_analytics.py` | H3 analytics transform functions |
| `test_export.py` | CSV/GeoJSON export correctness |

## Data Pipeline

### Adding New Data

1. Place detector summary files (`*_Summary.txt`) in the `data/` folder
2. Run the full pipeline:

```bash
docker compose exec api bash -c "
  uv run python -m app.backend.eti.cli_import /data && \
  uv run python -m app.backend.eti.pipeline /data /data/analytics --skip-import && \
  uv run python -m app.backend.eti.upload_all_to_s3
"
```

This imports files → generates H3 analytics → uploads everything to MinIO.

### Pipeline Options

```bash
# Full pipeline (import + H3 + CSV + GeoJSON exports)
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics --csv --geojson

# Skip import (use existing DB data, only regenerate H3 analytics)
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics --skip-import

# Custom date range and resolution
docker compose exec api uv run python -m app.backend.eti.pipeline \
  /data /data/analytics --start 2024-05-16 --end 2024-06-01 --resolution 8

# Skip H3 generation (import only + export)
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics --skip-h3 --csv
```

### Individual Steps

**1. Import Detector Summary Files**
```bash
docker compose exec api uv run python -m app.backend.eti.cli_import /data
```

**2. Generate H3 Analytics**
```bash
docker compose exec api uv run python -m app.backend.eti.pipeline /data /data/analytics --skip-import
```

Or using the standalone H3 CLI:
```bash
docker compose exec api uv run python -m app.backend.eti.transform.cli_h3_analytics \
  /data/analytics/h3_daily --start 2024-05-16 --end 2024-05-17 --resolution 7
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

## Data Source Configuration

Control where endpoints read data via environment variables in `.env`:

```bash
#Use MinIO for all endpoints
HEATMAP_SOURCE=s3

#Or per-endpoint
HEATMAP_POINTS_SOURCE=s3   # /api/heatmap/points
HEATMAP_H3_SOURCE=s3       # /api/heatmap/h3_parquet
```

| Variable | Values | Default |
|----------|--------|---------|
| `HEATMAP_SOURCE` | `db`, `local`, `s3` | — (shared fallback) |
| `HEATMAP_POINTS_SOURCE` | `db`, `local`, `s3` | `db` |
| `HEATMAP_H3_SOURCE` | `db`, `local`, `s3` | `local` |

Resolution order: per-endpoint var → `HEATMAP_SOURCE` → defaults.

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
#SQL shell
docker compose exec db psql -U app -d app

#Quick query
docker compose exec db psql -U app -d app -c "SELECT COUNT(*) FROM maug_summary_samples;"

#Python access
docker compose exec api uv run python
>>> from app.backend.eti.db import SessionLocal
>>> from app.backend.eti.models import MaugSummarySample
>>> db = SessionLocal()
>>> db.query(MaugSummarySample).count()
```

## Tests and Quality

```bash
# All tests in container
docker compose exec api uv run pytest -q

# Local dev (from backend)
cd backend
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -q
```

CI runs two jobs:
1. **Lint / Type-check / Unit tests** — `ruff check`, `ruff format --check`, `mypy`, `pytest` in `backend/` using uv
2. **Compose integration** — starts full Docker stack, waits for `/health`, syncs dev deps inside the container, runs pytest

## Repo Layout

```
.
├── docker-compose.yml              # PostGIS, Redis, MinIO, FastAPI
├── .env                            # Environment overrides (HEATMAP_SOURCE=s3)
├── .github/workflows/ci.yml        # Two-job CI: lint+test, compose integration
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml              # Dependencies (uv, Python 3.11)
│   ├── pytest.ini                  # Pytest configuration
│   ├── USAGE.md                    # Complete command reference
│   └── app/
│       ├── main.py                 # FastAPI app — all routes, models, caching
│       ├── static/
│       │   └── index.html          # MapLibre UI (timeline, hexagons, popups)
│       └── backend/
│           ├── eti/
│           │   ├── db.py                    # SQLAlchemy engine + session factory
│           │   ├── models.py                # MaugSummarySample ORM model
│           │   ├── s3.py                    # MinIO/S3 client helpers
│           │   ├── pipeline.py              # Full ETL orchestrator + CLI
│           │   ├── cli_import.py            # CLI: import summary files
│           │   ├── upload_all_to_s3.py      # CLI: upload all data to MinIO
│           │   ├── export_to_s3.py          # CLI: upload combined CSV to S3
│           │   ├── extract/
│           │   │   └── summary_import.py    # Parser for *_Summary.txt files
│           │   ├── transform/
│           │   │   ├── h3_analytics.py      # H3 × time aggregation to Parquet
│           │   │   └── cli_h3_analytics.py  # CLI for standalone H3 analytics
│           │   └── load/
│           │       ├── export.py            # CSV export function
│           │       ├── geojson_export.py    # GeoJSON export function
│           │       ├── cli_export.py        # CLI: export to CSV
│           │       └── cli_geojson_export.py # CLI: export to GeoJSON
│           └── tests/
│               ├── test_health.py
│               ├── test_timeline.py
│               ├── test_export.py
│               ├── test_h3_analytics.py
│               ├── test_heatmap_points_s3.py
│               ├── test_h3_parquet_s3.py
│               └── test_heatmap_minio_shared_flag.py
├── data/
│   ├── Summary Files/              # Detector summary files (*_Summary.txt)
│   ├── exports/                    # CSV/GeoJSON outputs
│   └── analytics/h3_daily/         # Pre-computed H3 Parquet files
└── db/
    └── init.sql                    # PostGIS extension + table schema
```

## Documentation

See [backend/USAGE.md](backend/USAGE.md) for the command reference:
All import/export commands
SQL queries and Python database access
Pipeline automation options
Environment variables
