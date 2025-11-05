# Copilot instructions for HeatmapBat

These notes make AI agents productive quickly in this repo by capturing the actual architecture, workflows, and project-specific patterns.

## Big picture
- Orchestration: `docker-compose.yml` runs a local stack: PostGIS (db), Redis, MinIO (S3-compatible), and the FastAPI backend (service `api`).
- Backend: FastAPI app at `backend/app/main.py` exposes basic ops endpoints (`/health`, `/live`).
- ETL: Placeholder scaffold under `backend/app/backend/eti/**` (extract/transform/load namespaces); not yet wired into the API.
- Storage/queues: DB URL, Redis URL, and S3 credentials are injected via environment (see compose). A `db/init.sql` enables PostGIS in the app database.

## Repo layout (key paths)
- `backend/app/main.py` — FastAPI app root with Pydantic models and routes.
- `backend/app/backend/tests/test_health.py` — pytest sample using `fastapi.testclient` against `app.main`.
- `backend/app/backend/eti/` — ETL scaffold (see Gotchas about `__init__` naming).
- `backend/.github/workflows/ci.yml` — CI runs uv + ruff + mypy + pytest in `backend/` working directory.
- `backend/Dockerfile` — Python 3.11 slim + uv; copies `app/`, but also references `etl/` and `tests/` (see Gotchas).

## Run the stack (dev)
- Start all services and hot-reload API:
```bash
docker compose up -d --build
# API readiness
curl http://localhost:8000/health
# MinIO console: http://localhost:9001 (minioadmin/minioadmin)
```
- The API container runs: `uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000` and mounts `./backend` to `/app` for live editing.

## Tests, lint, type-check
- Run tests inside the `api` container:
```bash
docker compose exec api uv run pytest -q
```
- CI indicates the canonical tooling/commands:
```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check
uv run mypy .
uv run pytest -q
```

## Conventions and patterns
- API routes live in `backend/app/main.py`. Use Pydantic models for responses (example below) and tag ops routes with `tags=["ops"]`.
- Tests import directly from `app.main` (module path rooted at `backend/app`). Example:
```python
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
```
- Package manager is [uv] with `uv run ...` for tool execution. Dependencies normally come from `pyproject.toml`/`uv.lock` (see Gotchas).

## Integration points (compose env)
- `DATABASE_URL=postgresql+psycopg://app:app@db:5432/app` (PostGIS already enabled via `db/init.sql`).
- `REDIS_URL=redis://redis:6379/0`.
- `S3_ENDPOINT_URL=http://minio:9000`, creds `minioadmin/minioadmin`, `S3_BUCKET=heatmapbat` (bucket creation not automated).

## Gotchas / current state
- `backend/pyproject.toml` is empty and `backend/uv.lock` is minimal; ensure deps (e.g., `fastapi`, `uvicorn`, `httpx`, `pytest`, `ruff`, `mypy`) are defined when adding features. Keep Python version in lock consistent with Dockerfile (3.11).
- ETL packages previously used `_init_.py` instead of `__init__.py`; this has been corrected. The odd nested `backend/app/backend/eti/backend/eti/load/` path has been flattened to `backend/app/backend/eti/load/`.
- `backend/Dockerfile` copies `etl/` and `tests/` at the repo layer, but the actual scaffolds live under `backend/app/backend/...`. Runtime uses a bind mount (`./backend:/app`), so local edits still reflect, but be mindful when building images without the bind.
- README mentions `pre-commit` but no `.pre-commit-config.yaml` is present.

## Example: add a simple route + test
- Route (in `backend/app/main.py`):
```python
@app.get("/ping")
def ping():
    return {"pong": True}
```
- Test (in `backend/app/backend/tests/test_ping.py`):
```python
from fastapi.testclient import TestClient
from app.main import app

def test_ping():
    r = TestClient(app).get("/ping")
    assert r.status_code == 200
    assert r.json() == {"pong": True}
```

## CI expectations
- PRs should pass ruff (lint/format), mypy (type-check), and pytest in the `backend` directory. Use the commands shown above locally (preferably in the container) to match CI.
