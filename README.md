## HeatmapBat

Geospatial occupancy playground: FastAPI backend, MapLibre UI, and optional MinIO/S3-backed heatmap data. Points and H3 hex bins can come from either the database/local files or object storage with caching and simple feature flags.

## Stack

- `docker-compose.yml` brings up PostGIS, Redis, MinIO, and the FastAPI app with hot reload.
- `backend/app/main.py` serves the web UI and heatmap APIs (`/api/heatmap/*`, `/health`, `/live`).
- `backend/app/backend/eti/` holds ETL code and S3 helpers (list/get/ensure bucket).
- `backend/app/backend/tests/` covers the API, S3 modes, and analytics endpoints.
- `.env` overrides compose env (including heatmap source flags) without editing YAML.

## Run the app (local dev)

```bash
# From repo root
docker compose up -d --build

# Health check and UI
curl http://localhost:8000/health
open http://localhost:8000/ || xdg-open http://localhost:8000/ || "$BROWSER" http://localhost:8000/
```

- The API runs `uv run fastapi dev app/main.py` and reloads on code changes.
- Compose injects DB/Redis/S3 endpoints; bind mounts `./backend` and `./data` into the container.
- MinIO console: http://localhost:9001 (minioadmin/minioadmin).

## Data modes and feature flags

- Defaults: points from PostGIS (`db`), H3 Parquet from local filesystem (`data/analytics/h3_daily`).
- Shared toggle: `HEATMAP_SOURCE=s3` switches both points and H3 reads to MinIO/S3.
- Per-endpoint overrides: `HEATMAP_POINTS_SOURCE` and `HEATMAP_H3_SOURCE` (`db`/`local`/`s3`).
- Object keys/paths: `points_object` query param (default `data/exports/maug_points.geojson`); `analytics_dir` query param (default `data/analytics/h3_daily`).
- Caching: in-memory 5-minute TTL for S3 objects; bucket auto-created at startup when S3 mode is enabled.

## Uploading data to MinIO

- Bucket: `heatmapbat` (auto-ensured on API startup). Access via the MinIO console at http://localhost:9001.
- Points: upload a GeoJSON or CSV to `data/exports/maug_points.geojson` (or another key you pass via `points_object`).
- H3 analytics: upload date-partitioned Parquet under `data/analytics/h3_daily/<YYYY>/<MM>/<DD>/...` (prefix overridable via `analytics_dir`).
- If you prefer CLI, set `AWS_ACCESS_KEY_ID=minioadmin`, `AWS_SECRET_ACCESS_KEY=minioadmin`, and `AWS_ENDPOINT_URL=http://localhost:9000`, then use `aws s3 cp` or `aws s3 sync` against bucket `heatmapbat`.

## API quick reference

- `GET /health`, `GET /live`: lightweight probes.
- `GET /api/heatmap/points?start&end&points_object=`
  - Sources from PostGIS or S3/local GeoJSON/CSV based on flags.
  - Returns `lat`, `lon`, `raw_count`, `effort_normalised_weight`, `timestamp_utc`.
- `GET /api/heatmap/h3?start&end&resolution=`
  - Server-side binning via H3 using PostGIS-backed samples.
- `GET /api/heatmap/h3_parquet?start&end&analytics_dir=`
  - Reads precomputed H3 Parquet locally or from MinIO/S3 (honors `HEATMAP_H3_SOURCE`).

## Tests and quality

```bash
# All tests (inside container)
docker compose exec api uv run pytest -q

# Focused S3 tests
docker compose exec api uv run pytest -q app/backend/tests/test_heatmap_points_s3.py app/backend/tests/test_h3_parquet_s3.py app/backend/tests/test_heatmap_minio_shared_flag.py

# Lint/type-check (from backend/)
cd backend
uv run ruff check .
uv run mypy .
uv run pytest -q
```

## Repo layout (trimmed)

```
.
├── docker-compose.yml
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── pytest.ini
│   ├── USAGE.md
│   └── app/
│       ├── main.py
│       └── backend/
│           ├── eti/                # ETL + S3 helpers
│           └── tests/              # pytest suite (health + S3 + analytics)
├── data/                           # sample inputs and exports
└── db/init.sql                     # PostGIS enablement
```

## TODO

- Advanced occupancy modeling: add hex-bin visualisation and richer heatmaps (e.g., wind direction or other covariates) to improve occupancy inference.
- Email mko to request longer-term data coverage for modeling.
- Make hex-bin rendering a first-class option alongside point heatmaps.
- Cross-spatial occupancy view and click-to-drill remain on deck.
