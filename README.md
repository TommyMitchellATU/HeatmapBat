## Project overview

- Root `docker-compose.yml`: orchestrates the local stack:
	- `db` (Postgres + PostGIS) with init script `db/init.sql` enabling the PostGIS extension
	- `redis` (Redis 7) for caching/queues
	- `minio` (S3-compatible storage) with console at http://localhost:9001
	- `api` (FastAPI app in `backend/app/main.py`), hot-reloading with your local source mounted
- `backend/Dockerfile`: builds the API container on Python 3.11 and installs dependencies using `uv`
- `backend/pyproject.toml`: declares Python dependencies and tool configs (ruff/mypy/pytest)
- `backend/app/main.py`: FastAPI app with operations endpoints (`/health`, `/live`)
- `backend/app/backend/tests/test_health.py`: example pytest exercising `/health`
- `backend/app/backend/eti/**`: ETL scaffold (extract/transform/load) — placeholder modules not yet wired into the API
- `.pre-commit-config.yaml`: pre-commit hooks for formatting, linting, and basic hygiene
- `.gitattributes`: normalize line endings
- `.env` (optional): overrides environment variables for `docker compose`

## Project structure

```
.
├── docker-compose.yml
├── backend/
│   ├── Dockerfile                  # API container (Python 3.11 + uv)
│   ├── pyproject.toml              # Python deps + tool configs (ruff/mypy/pytest)
│   └── app/
│       ├── main.py                 # FastAPI app (/health, /live)
│       └── backend/
│           ├── eti/                # ETL scaffold (package importable as backend.eti)
│           │   ├── __init__.py
│           │   ├── __main__.py
│           │   ├── cli_import.py
│           │   ├── db.py
│           │   ├── models.py
│           │   ├── pipeline.py     # future run_etl orchestration
│           │   ├── extract/
│           │   │   ├── __init__.py
│           │   │   └── summary_import.py
│           │   ├── transform/
│           │   │   └── __init__.py
│           │   └── load/
│           │       └── __init__.py
│           └── tests/
│               └── test_health.py  # pytest hitting /health
├── data/
│   └── MAUG-1397_A_Summary.txt     # example raw input
└── db/
    └── init.sql                    # enables PostGIS extension
```

## Services and flow

- `docker-compose.yml`
	- Starts 4 containers for local dev:
		- `db` = Postgres with PostGIS (spatial database)
		- `redis` = in-memory cache/queue
		- `minio` = S3-compatible file storage (with a web console)
		- `api` = Python FastAPI app, with auto-reload for quick iteration

- `backend/app` (Python package `app`)
	- `main.py`: minimal web server. Exposes `/health` and `/live` for checks.
	- `backend/eti/`: ETL scaffold (read/clean/write data). Separate from the web app so batch jobs stay decoupled.
		- `extract/`: functions that will read raw inputs (CSV/TXT/metadata)
		- `transform/`: pure data transformations (timestamps, geotagging, features)
		- `load/`: writers to Parquet, PostGIS, and S3 (planned)
		- `pipeline.py`: future `run_etl` function to coordinate extract → transform → load
	- `backend/tests/`: example tests that call the API in-process

- `db/init.sql`
	- Enables PostGIS in the local database so you can store/query geometry types used in heatmaps.

## Running the stack

```bash
# Build images and start services in the background
docker compose up -d --build

# Check API health (FastAPI dev server, port 8000)
curl http://localhost:8000/health

# Inspect PostGIS installation (optional)
docker compose exec db psql -U app -d app -c "SELECT PostGIS_Full_Version();"

# Open MinIO console (optional)
xdg-open http://localhost:9001 || "$BROWSER" http://localhost:9001
```

How it works:
- The API container runs `uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000`
- `./backend` is bind-mounted to `/app` in the container for hot reload while you edit code locally
- Environment variables for DB/Redis/S3 are injected by compose (and can be overridden via `.env`)

## Tests and quality

```bash
# Run the test suite inside the API container
docker compose exec api uv run pytest -q

# Lint, format-check, and type-check (from backend/)
cd backend
uv run ruff check .
uv run mypy .
uv run pytest -q

# Run pre-commit hooks locally (format, lint, basic checks)
pre-commit run --all-files
```

## High-level data flow (planned)

- Extract raw files/metadata from `data/` and external sources
- Transform/enrich (timestamps, geotagging, features) in `backend/app/backend/eti/transform`
- Load partitioned Parquet to S3/MinIO and geometries/aggregates to PostGIS
- Optionally queue jobs/status via Redis

## Explanation of each part

HeatmapBat aims to process geospatial time-series at scale and serve aggregated insights. The current structure sets us up for reliability and speed:

### docker-compose.yml (root)

- A single file that starts four containers that work together: the web API, a database (Postgres+PostGIS), a cache/queue (Redis), and file storage (MinIO). This runs the same setup with one command, without installing databases or services.

### backend/Dockerfile

Settings to build the API container (based on Python 3.11) and install Python dependencies with uv. This guarantees the API runs with the same Python and libraries everywhere (dev/CI/prod).
Testing: `docker compose build api` then start the stack; the API should report healthy at `/health`.

### backend/pyproject.toml

The list of Python packages (FastAPI, Uvicorn, etc.) and tool configs (ruff, mypy, pytest).
Start the `api` service; it runs `uv sync` to install what's in this file. Lint/type-check/test commands in CI read their settings from here.

### backend/app/main.py (API)

Turns Python functions into URLs you can call. Currently just for health checks but will add functionality later.
 `curl http://localhost:8000/health` should return JSON with `ok: true`.

### backend/app/backend/eti/** (ETL scaffold)

Read raw inputs, transform, and write outputs (to S3/DB/etc.). Seperates processing from the fast web API.
`backend/app/backend/eti/` with `extract/`, `transform/`, `load/`, and `pipeline.py`.

### backend/app/backend/tests/** (tests)

Automated checks to prove the app behaves as expected.

### db/init.sql (database setup)

A SQL script that enables the PostGIS extension in the local Postgres database. We need geospatial types and functions for heatmaps.

### .env (environment overrides)

A local file where you can override environment variables that compose passes to services. Lets you change credentials/URLs/feature flags without editing the compose file.

### .pre-commit-config.yaml (git hooks)

Rules that run on `git commit` to auto-fix line endings, format/lint code, and reports mistakes (this is literal magic).

### .gitattributes (line endings)

A Git setting file that forces LF line endings in the repo regardless of OS defaults.
Git diffs shouldn't show random EOL-only changes when switching branches or collaborating.

### .github/workflows/ci.yml (CI pipeline)

Runs in GitHub on every push/PR: installs deps, lints, type-checks, and runs tests.

## Glossary

- FastAPI: A Python web framework for building APIs quickly.  (web server that turns Python functions into HTTP endpoints)
- Uvicorn: A fast web server (ASGI) that runs the FastAPI app.
- Pydantic: Validates and serializes data for API.
- PostGIS: Spatial extension for Postgres that adds geometry types and geospatial functions (heatmaps, distance, etc.).
- Redis: In‑memory key/value store used for caching and simple queues.
- MinIO: Local, S3‑compatible object storage. Great for dev; maps to AWS S3 in production.
- S3 (object storage): Stores files/blobs (like Parquet outputs) rather than rows in a database.
- ETL: Extract → Transform → Load. Read raw inputs, clean, and write outputs to storage/DB.
- Docker Compose: One YAML file that starts multiple containers together for local dev.
- uv: A fast Python package manager/runner used here instead of pip for speed and consistency.
- pytest: Test runner for Python. Runs your tests and reports results.
- ruff: Fast linter/formatter for Python code style and simple issues.
- mypy: Type checker that finds mistakes by validating function signatures and variable types.
