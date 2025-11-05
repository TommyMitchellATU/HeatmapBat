## Project overview

- Root `docker-compose.yml` (repo root): Orchestrates the local stack:
	- `db` (Postgres + PostGIS) with init script `db/init.sql` enabling the PostGIS extension
	- `redis` (Redis 7) for caching/queues
	- `minio` (S3-compatible storage) with console at http://localhost:9001
	- `api` (this FastAPI app), hot-reloading with your local source mounted
- `backend/Dockerfile`: Builds the API container on Python 3.11 and installs dependencies using uv
- `backend/pyproject.toml`: Declares Python dependencies and tool configs (ruff/mypy/pytest)
- `backend/app/main.py`: FastAPI app with operations endpoints (`/health`, `/live`)
- `backend/app/backend/tests/test_health.py`: Example pytest exercising `/health`
- `backend/app/backend/eti/**`: ETL scaffold (extract/transform/load) — placeholder modules not yet wired into the API
- `backend/.github/workflows/ci.yml`: CI pipeline that runs lint, type-check, and tests within `backend/`

## Project structure

```
backend/
	Dockerfile                  # API container (Python 3.11 + uv)
	pyproject.toml              # Python deps + tool configs (ruff/mypy/pytest)
	app/
		main.py                   # FastAPI app (/health, /live)
		backend/
			eti/                    # ETL scaffold (package now importable)
				__init__.py
				pipeline.py           # run_etl(input_dir, output_dir)
				extract/
					__init__.py         # readers for raw sources
				transform/
					__init__.py         # normalization / features
				load/
					__init__.py         # loaders (parquet, PostGIS, S3) — placeholder
			tests/
				test_health.py        # sample pytest hitting /health
```

## Project structure

- docker-compose.yml (repo root)
	- One file that starts everything for local dev. It runs 4 containers:
		- db = Postgres with PostGIS (spatial database)
		- redis = in‑memory cache/queue
		- minio = S3-compatible file storage (with a web console)
		- api = this Python FastAPI app, with auto‑reload for quick iteration

- backend/
	- Dockerfile: recipe to build the api container (Python 3.11 + uv package manager)
	- pyproject.toml: where Python dependencies (fastapi, uvicorn, etc.) and tools (pytest, ruff, mypy) are declared
	- app/ (Python package named "app")
		- main.py: the minimal web server. It exposes /health and /live for checks.
		- backend/eti/: ETL scaffold (read/clean/write data). It’s a separate package so data jobs don’t mix with the web app.
			- extract/: functions that read raw inputs (CSV/TXT/metadata)
			- transform/: pure data transformations (timestamps, geotagging, features)
			- load/: writers to Parquet, PostGIS, and S3
			- pipeline.py: a single function (run_etl) that coordinates extract→transform→load
		- backend/tests/: example tests that call the API in‑process
	- .github/workflows/ci.yml: CI pipeline that installs deps, lints, type‑checks, and runs tests

- db/init.sql
	- Enables PostGIS in the local database so you can store/query geometry types used in heatmaps.

- .env
	- Environment variables loaded by docker compose (for example, credentials or overrides). If missing, defaults are used from compose.

- .pre-commit-config.yaml
	- Git hooks that run on commit to keep diffs clean (line endings), code formatted, and basic issues caught early.

- .gitattributes
	- Ensures consistent LF line endings across OSes (prevents CRLF/LF flip‑flop on Windows).

```bash
# Build images and start services in the background
docker compose up -d --build

# Check API health (FastAPI dev server, port 8000)
curl http://localhost:8000/health

# Inspect PostGIS installation (optional)
docker compose exec db psql -U app -d app -c "SELECT PostGIS_Full_Version();"

# Open MinIO console (optional): http://localhost:9001 (user: minioadmin / pass: minioadmin)
```

How it works:
- The API container runs `uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000`
- `./backend` is bind-mounted to `/app` in the container for hot reload while you edit code locally
- Environment variables for DB/Redis/S3 are injected by compose

## Tests
```bash
# Run the test suite inside the API container
docker compose exec api uv run pytest -q

# Run pre-commit hooks locally (format, lint, basic checks)
pre-commit run --all-files


flow:
- Extract raw files/metadata, transform/enrich (timestamps, geotagging, features)
- Load partitioned Parquet to S3 (MinIO) and geometries/aggregates to PostGIS
- Optionally queue jobs/status via Redis

## Troubleshooting

- API not responding on http://localhost:8000/health
	- Check logs: `docker compose logs -f api`
	- Ensure deps are installed (container does `uv sync` from `pyproject.toml`). We require `fastapi[standard]` for the `fastapi dev` CLI.
- Port conflicts
	- Postgres: 5432, Redis: 6379, API: 8000, MinIO: 9000/9001. Stop local services or change ports in `docker-compose.yml`.
- Python version / lock mismatch
	- Dockerfile uses Python 3.11. If you add a lockfile later, regenerate it under 3.11 to match the image.
- Windows CRLF line endings
	- The repo uses `.gitattributes` to enforce LF. If hooks fix line endings during commit, re-run: `git add -A` then `git commit -m ...`.

```

## Explenation of each part

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
