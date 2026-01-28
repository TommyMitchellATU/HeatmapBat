# Copilot instructions for HeatmapBat

These notes help AI agents be immediately productive in this repo by capturing architecture, workflows, and project-specific patterns.

## Big picture
- **Purpose**: Geospatial occupancy playground for bat detection data; MapLibre-powered heatmap UI consuming flexible data sources (DB/local/S3).
- **Orchestration**: `docker-compose.yml` runs PostGIS, Redis, MinIO (S3-compatible), and FastAPI backend with hot-reload (`./backend:/app` bind mount).
- **Data flow**: MAUG summary files → `MaugSummarySample` table → heatmap APIs (raw points + H3 aggregates). Data can be served from PostGIS, local files, or S3 (MinIO) based on env flags.
- **Key architecture decision**: Single-file FastAPI app (`backend/app/main.py`) with all routes inline; ETL modules under `backend/app/backend/eti/` are standalone and not yet integrated into the API.

## Repo layout
- `backend/app/main.py` — FastAPI app, heatmap routes, static file serving, and data-source resolution logic (~535 lines).
- `backend/app/backend/eti/` — ETL modules: `models.py` (SQLAlchemy ORM for `maug_summary_samples`), `db.py` (session factory), `s3.py` (MinIO/S3 client wrappers), `pipeline.py` (placeholder orchestration).
- `backend/app/backend/tests/` — pytest suite: health checks, S3 mode tests, analytics endpoints. Tests use `fastapi.testclient` and `monkeypatch` for env flags.
- `data/` — Sample MAUG summary files, exports (CSV/GeoJSON), and analytics (H3 Parquet partitioned by date under `analytics/h3_daily/YYYY/MM/DD/`).
- `.github/workflows/ci.yml` — Two jobs: 1) lint/type/unit tests (uv sync + ruff + mypy + pytest in `backend/` working directory), 2) compose integration (docker stack + health check + in-container pytest).

## Run the stack (dev)
```bash
# Start all services with hot-reload
docker compose up -d --build
curl http://localhost:8000/health
# Web UI: http://localhost:8000/
# MinIO console: http://localhost:9001 (minioadmin/minioadmin)

# Run tests inside container (matches CI)
docker compose exec api uv run pytest -q

# Focused S3 tests
docker compose exec api uv run pytest -q app/backend/tests/test_heatmap_points_s3.py app/backend/tests/test_h3_parquet_s3.py
```

## Data source flexibility (key pattern)
- **Env flags**: `HEATMAP_SOURCE=s3` (shared override), `HEATMAP_POINTS_SOURCE`, `HEATMAP_H3_SOURCE` (per-endpoint; valid values: `db`, `local`, `s3`).
- **Precedence**: per-endpoint var → `HEATMAP_SOURCE` → defaults (`points=db`, `h3=local`).
- **Resolution function**: `_resolve_source(point_var, hex_var)` in `backend/app/main.py` implements this logic; used by `/api/heatmap/points` and `/api/heatmap/h3_parquet`.
- **Caching**: in-memory 5-minute TTL for S3 objects (`_object_cache` dict in `main.py`); bucket auto-created at startup via `ensure_bucket_exists()` (see `@app.on_event("startup")`).
- **Test pattern**: use `monkeypatch.setenv("HEATMAP_POINTS_SOURCE", "s3")` to test S3 mode without mocking boto3; see `test_heatmap_minio_shared_flag.py` for full example using a fake S3 client.

## API routes and Pydantic models
- **Ops**: `/health`, `/live` → `HealthResponse(status="ok")` tagged with `tags=["ops"]`.
- **Heatmap endpoints**:
  - `/api/heatmap/points` → `List[HeatmapPoint]` (DB or S3 GeoJSON/CSV; honors `HEATMAP_POINTS_SOURCE`).
  - `/api/heatmap/h3` → `List[H3Cell]` (server-side H3 binning from PostGIS; always uses DB).
  - `/api/heatmap/h3_parquet` → `List[H3ParquetCell]` (precomputed Parquet from `data/analytics/h3_daily` or S3; honors `HEATMAP_H3_SOURCE`).
- **Weighting pattern**: `HeatmapPoint` exposes `raw_count` (underlying `files_count` from DB) and `effort_normalised_weight` (placeholder for effort adjustment; currently equals `raw_count`). This keeps the API stable for future effort-aware weighting.

## Tests and quality
- **Package manager**: [uv](https://github.com/astral-sh/uv) — use `uv run ...` for all tool execution.
- **Lint/type/test commands** (from `backend/` directory):
```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -q
```
- **Import path**: tests import from `app.main` (module path rooted at `backend/app`), e.g.:
```python
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
```
- **CI**: two-job workflow. Lint/type/unit runs in `backend/` with uv; compose-integration starts full stack, waits for `/health`, syncs dev deps inside container, then runs pytest.

## Integration points (compose env)
- `DATABASE_URL=postgresql+psycopg://app:app@db:5432/app` (PostGIS enabled via `db/init.sql`).
- `REDIS_URL=redis://redis:6379/0` (not yet wired into app; placeholder for caching/queues).
- `S3_ENDPOINT_URL=http://minio:9000`, `S3_ACCESS_KEY_ID=minioadmin`, `S3_SECRET_ACCESS_KEY=minioadmin`, `S3_BUCKET=heatmapbat`.
- **Override via `.env`**: create `.env` in repo root to override compose env vars (e.g., `HEATMAP_SOURCE=s3`) without editing YAML.

## Adding a new route
1. Define Pydantic response model in `backend/app/main.py` (see `HeatmapPoint`, `H3Cell`).
2. Add route using `@app.get("/path", response_model=Model, tags=["category"])`.
3. If using DB, inject session via `db: Session = Depends(get_db)` and import from `app.backend.eti.db`.
4. Create test in `backend/app/backend/tests/test_<feature>.py`:
```python
from fastapi.testclient import TestClient
from app.main import app

def test_new_route():
    r = TestClient(app).get("/path")
    assert r.status_code == 200
```

## Common gotchas
- **ETL modules**: `backend/app/backend/eti/pipeline.py` is a placeholder; not called by the API. Actual data import/export logic exists in `extract/`, `transform/`, `load/` subpackages but is CLI-driven (see `cli_import.py`, `cli_export.py`).
- **Dependencies**: defined in `backend/pyproject.toml` (`dependencies` for runtime, `[tool.uv].dev-dependencies` for lint/test). Keep Python version (3.11) consistent with `backend/Dockerfile`.
- **Static files**: `backend/app/static/index.html` served at `/` via `app.mount("/", StaticFiles(...), name="static")`.
- **H3 resolution**: `/api/heatmap/h3` query param `resolution` defaults to 7; valid range 0-15 (6-8 reasonable for country-scale maps).
