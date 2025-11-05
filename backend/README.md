# HeatmapBat backend (FastAPI + ETL)

This folder contains the backend service for HeatmapBat. It's Dockerized for local development and includes a FastAPI app plus a placeholder ETL scaffold.

## Project overview (what each part does)

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

## Run locally (Docker)
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
- Environment variables for DB/Redis/S3 are injected by compose so you can wire integrations later

## Tests & lint
```bash
# Run the test suite inside the API container
docker compose exec api uv run pytest -q

# Run pre-commit hooks locally (format, lint, basic checks)
pre-commit run --all-files
```

What these do:
- `pytest` uses `fastapi.testclient` to hit routes (see `backend/app/backend/tests/test_health.py`)
- `pre-commit` runs code hygiene (line endings, trailing whitespace) and ruff (lint/format)
	- Type checks (mypy) run in CI by default; the pre-commit hook may be enabled once package layout stabilizes

## ETL (placeholder)
```bash
# Placeholder example (not wired up yet); folders are scaffolds only
# Import via the app package path (now importable)
docker compose exec api uv run python -c "from app.backend.eti.pipeline import run_etl; import pathlib; run_etl(pathlib.Path('/data/in'), pathlib.Path('/data/out'))"
```

Intended flow (future):
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

## Why this robustness?

HeatmapBat aims to process geospatial time-series at scale and serve aggregated insights. The current structure sets us up for reliability and speed:

- Reproducible environments
	- Docker Compose brings PostGIS, Redis, and S3-compatible storage locally, so integration points are exercised early.
	- Python version is pinned (3.11) across Docker and CI to avoid “works on my machine”.
- Strong developer feedback loops
	- Hot reload via `fastapi dev` keeps iteration tight.
	- Pre-commit hooks (EOLs, lint/format) prevent noisy diffs and cross-OS issues.
	- CI runs ruff, mypy, and pytest to gate regressions.
- Clear separation of concerns
	- API stays small and responsive; ETL stages (extract/transform/load) live in their own package with clean contracts.
	- Side effects (DB/S3) are funneled through loaders, making it easy to swap destinations (local/S3) or batch/stream mechanics later.
- Future-ready for scale
	- PostGIS supports spatial indices/queries for heatmaps; Redis can handle caching/queues; MinIO stands in for S3 in dev but maps to cloud in prod.
	- The ETL pipeline can be scheduled (cron/Prefect) without entangling the web API.

In short, this scaffolding avoids rework when data volumes and features grow, while remaining simple enough for fast local development today.
