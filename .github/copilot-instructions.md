# Copilot instructions for HeatmapBat

These notes help AI agents be immediately productive in this repo by capturing architecture, workflows, and project-specific patterns.

## Big picture
- **Purpose**: Geospatial occupancy playground for bat detection data; MapLibre-powered heatmap UI consuming flexible data sources (DB/local/S3), plus a Bayesian occupancy-modelling notebook implementing research papers.
- **Orchestration**: `docker-compose.yml` runs PostGIS, Redis, MinIO (S3-compatible), and FastAPI backend with hot-reload (`./backend:/app` bind mount).
- **Data flow**: Detector summary files → `MaugSummarySample` table → heatmap APIs (raw points + H3 aggregates). Data can be served from PostGIS, local files, or S3 (MinIO) based on env flags. H3 daily Parquet files also feed the occupancy modelling notebook.
- **Key architecture decision**: Single-file FastAPI app (`backend/app/main.py`) with all routes inline; ETL modules under `backend/app/backend/eti/` are standalone and not yet integrated into the API.
- **Current work-in-progress**: Implementing research papers as Bayesian occupancy models in `occupancy_modeling.ipynb` (see "Occupancy modelling notebook" section below).

## Repo layout
- `backend/app/main.py` — FastAPI app, heatmap + timeline routes, static file serving, and data-source resolution logic (~860 lines).
- `backend/app/backend/eti/` — ETL modules:
  - `models.py` (SQLAlchemy ORM for `maug_summary_samples`), `db.py` (session factory), `s3.py` (MinIO/S3 client wrappers), `pipeline.py` (placeholder orchestration).
  - `extract/summary_import.py` — summary file ingestion.
  - `transform/h3_analytics.py`, `transform/cli_h3_analytics.py` — H3 spatial aggregation and CLI entry point.
  - `load/export.py`, `load/geojson_export.py`, `load/cli_export.py`, `load/cli_geojson_export.py` — CSV/GeoJSON export logic and CLIs.
  - `export_to_s3.py`, `upload_all_to_s3.py` — S3 upload utilities.
  - `cli_import.py` — CLI-driven data import.
- `backend/app/backend/tests/` — pytest suite (7 test files): `test_health.py`, `test_heatmap_points_s3.py`, `test_h3_parquet_s3.py`, `test_heatmap_minio_shared_flag.py`, `test_h3_analytics.py`, `test_export.py`, `test_timeline.py`.
- `backend/app/static/index.html` — SPA frontend (MapLibre heatmap + timeline slider).
- `occupancy_modeling.ipynb` — Bayesian occupancy modelling notebook (28 cells; PyMC/ArviZ). See dedicated section below.
- `data/Summary Files/` — Raw bat detector summary text files (~60 files, naming: `SITE-ID_A_Summary.txt`).
- `data/exports/` — Pre-exported CSV (`maug_points_2024-05-16.csv`) and GeoJSON (`maug_points.geojson`).
- `data/analytics/h3_daily/` — Pre-computed daily H3 Parquet files (`h3_analytics_YYYY-MM-DD.parquet`), ~125 files spanning 2024-05-07 to 2024-10-09.
- `.github/workflows/ci.yml` — Two jobs: 1) lint/type/unit tests (uv sync + ruff + mypy + pytest in `backend/` working directory), 2) compose integration (docker stack + health check + in-container pytest).
- `db/init.sql` — PostGIS-enabled database initialisation.
- `docker-compose.yml` — Full stack: PostGIS, Redis, MinIO, FastAPI.
- `.pre-commit-config.yaml` — Pre-commit hooks config.

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
  - `/api/heatmap/h3_parquet` → `List[H3ParquetCell]` (precomputed Parquet from `data/analytics/h3_daily` or S3; honors `HEATMAP_H3_SOURCE`). Supports `start`/`end` date query params.
- **Timeline endpoint**:
  - `/api/timeline/dates` → `TimelineResponse` (per-day `sample_count` + `total_detections` from DB; provides `min_date`/`max_date` for slider bounds).
- **UI**: `/` serves `index.html` (SPA); `/static` mounts static assets.
- **Weighting pattern**: `HeatmapPoint` exposes `raw_count` and `effort_normalised_weight` (placeholder; currently equals `raw_count`). `H3ParquetCell` includes effort-normalised fields: `detector_nights` (unique site-date pairs per cell), `detections_per_night` (raw_count_sum / detector_nights). The `_aggregate` function in `h3_analytics.py` performs a two-stage groupby to compute these metrics.

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

## Occupancy modelling notebook

`occupancy_modeling.ipynb` is the current active work area — implementing Bayesian occupancy models from the literature review using PyMC/ArviZ.

### Notebook structure (30 cells)
| Section | Cells | Description |
|---|---|---|
| 1. Setup & Imports | 1–3 | PyMC, ArviZ, NumPy, Pandas, h3; sets `PYTENSOR_FLAGS` for CPU-only |
| 2. Load Data & Detection Histories | 4–6 | Reads H3 daily Parquet from `data/analytics/h3_daily/`; builds `Y` (sites × dates binary detection), `K` (effort), `N` (raw counts) matrices |
| 3. EDA | 7–8 | Detection heatmap, per-site frequency, effort, temporal pattern, effort-vs-count scatter → saves `occupancy_eda.png` |
| 4. Covariates | 9–10 | Spatial (lat/lon from H3 centroids), temporal (DOY + DOY²), effort (log sample count); all standardised |
| 5. M1 — Single-season baseline | 11–12 | MacKenzie et al. 2002; global ψ, p; z marginalised (all 7 sites have detections) |
| 6. M2 — Single-season + covariates | 13–14 | ψ(lat, lon), p(effort, DOY, DOY²); z marginalised |
| 7. M3 — Misclassification-aware | 15–16 | Miller et al. 2011; adds `p_fp` (false-positive rate); z marginalised via logsumexp |
| 8. M4 — Dynamic occupancy | 17–19 | MacKenzie et al. 2003; weekly aggregation; colonisation (γ) + extinction (ε); **Binomial(J, p)** likelihood within weekly periods; pytensor.scan for ψ(t) trajectory → saves `occupancy_dynamic_trajectory.png` |
| 9. M5 — Spatial random effects | 20–21 | Johnson et al. 2013; MvNormal spatial random effects with exponential covariance |
| 10. M6 — Count-based detection | 22–23 | Royle & Dorazio 2008; NegBin likelihood on raw acoustic counts with overdispersion; uses count data instead of binary |
| 11. Spatial block CV | 24–25 | Leave-one-site-out CV (Roberts et al. 2017; Valavi et al. 2019); z marginalised; Brier score + log-likelihood |
| 12. Model comparison & diagnostics | 26–27 | R̂, ESS checks; WAIC comparison (M1/M2/M5 binary, M6 count); posterior histograms for M2 → saves `occupancy_posteriors.png` |
| 13. Spatial visualisation | 28–29 | Occupancy map, model comparison bar chart, dynamic trajectory overlay, CV Brier scores → saves `occupancy_results.png` |
| 14. Summary | 30 | Reference table mapping models to papers |

### Data pipeline into notebook
- Reads directly from `data/analytics/h3_daily/h3_analytics_*.parquet` (no API call needed).
- 7 detector sites (`site_id`), 125 nightly survey dates, ~125 Parquet files.
- Key fields from Parquet: `site_id`, `h3_index`, `raw_count_sum`, `sample_count`, `time_bin_start`.

### Model implementation patterns
- **z marginalisation**: Since all 7 sites have at least one detection, `z_i = 1` is certain for M1/M2/M5/M6. Models use `pm.Potential('psi_ll', ...)` instead of discrete `pm.Bernoulli('z', ...)` to avoid NUTS issues with discrete parameters.
- **M3 exception**: False positives mean detections don't guarantee occupancy → z marginalised via `pm.math.logsumexp` over `[ll_z1, ll_z0]`.
- **M4 dynamics**: Uses `pytensor.scan` for Markov ψ(t) trajectory; Binomial(J, p) likelihood marginalised over z using `pt.logaddexp` for numerical stability.
- **M6 counts**: Uses raw acoustic counts via `pm.NegativeBinomial` instead of binary detection; captures overdispersion in acoustic data.
- **Sampling**: All models use `cores=1`, `random_seed=42`, `target_accept=0.9–0.95`, 2000 draws + 1000 tune.
- **Output PNGs**: `occupancy_eda.png`, `occupancy_dynamic_trajectory.png`, `occupancy_posteriors.png`, `occupancy_results.png` (committed to repo root).

### Key references (shorthand used in notebook)
- [1] Arnett et al. 2008 — Bat fatality patterns at wind facilities
- [2] Baerwald & Barclay 2011 — Activity and fatality at Alberta wind facility
- [5] Hayes 2000 — Echolocation monitoring design considerations
- [6] MacKenzie et al. 2002 — Single-season occupancy (M1, M2)
- [7] Royle & Dorazio 2008 — Hierarchical modelling perspective; count-based detection (M6)
- [8] MacKenzie et al. 2003 — Dynamic occupancy (M4)
- [9] Miller et al. 2011 — False-positive occupancy models (M3)
- [11] Johnson et al. 2013 — Spatial occupancy for large datasets (M5)
- [12] Roberts et al. 2017 — Cross-validation for structured data
- [13] Valavi et al. 2019 — blockCV for spatial validation
- [14] Yates 2010 — Forest structure and bat occupancy
- [15] Ekman & de Jong 1996 — Patch isolation and bat distribution
