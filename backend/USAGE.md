# HeatmapBat Backend Usage Cheat Sheet

## Top-Level

- `docker-compose.yml`: Run full stack: FastAPI API, Postgres/PostGIS, Redis, MinIO.
  - Start stack:
    ```bash
    cd /workspaces/HeatmapBat
    docker compose up -d --build
    ```
  - Stop stack:
    ```bash
    docker compose down
    ```
- `db/init.sql`: Initializes Postgres with PostGIS extension and app schema.

---

## Backend Project (`backend/`)

- `Dockerfile`: Image for `api` service; runs FastAPI via `uv` (Python 3.11).
- `pyproject.toml`: Python project configuration and dependencies.
- `pytest.ini`: Pytest settings.

**Common commands (run from `backend/`):**

```bash
cd backend
uv sync                  # install dependencies
uv run pytest -q         # run tests
uv run ruff check .      # lint
uv run ruff format --check  # format check
uv run mypy .            # type-check
```

---

## FastAPI App (`backend/app/`)

- `app/main.py`: FastAPI application entrypoint.
  - Defines `app = FastAPI(...)`.
  - Exposes health endpoints like `/health`, `/live` (tag `"ops"`).

**Run dev server directly (no Docker):**

```bash
cd backend
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000
```

**Health check:**

```bash
curl http://localhost:8000/health
```

---

## Backend Package (`backend/app/backend/`)

- `backend/__init__.py`: Marks `backend` as a package.
- `backend/tests/`:
  - `test_health.py`: Example pytest using `TestClient` against `app.main`.

**Run this test only:**

```bash
cd backend
uv run pytest app/backend/tests/test_health.py -q
```

---

## ETI Subsystem (`backend/app/backend/eti/`)

- `eti/__init__.py`: Package marker.
- `eti/__main__.py`: Allows running ETI as a module (if extended).

Example (from `backend/`):

```bash
uv run python -m app.backend.eti
```

### 1. Database Helpers (`eti/db.py`)

- Provides a central SQLAlchemy engine and session factory.
- Key objects:
  - `DATABASE_URL`: from `DATABASE_URL` env (defaults to Postgres DSN).
  - `engine`: SQLAlchemy engine.
  - `SessionLocal`: `sessionmaker` for DB sessions.
  - `get_db()`: FastAPI-style dependency yielding a `Session`.

**Example usage:**

```python
from app.backend.eti.db import SessionLocal

db = SessionLocal()
try:
    # use db
    ...
finally:
    db.close()
```

### 2. ORM Models (`eti/models.py`)

- SQLAlchemy ORM models for ETI data.
- Key classes:
  - `Base`: Declarative base (`DeclarativeBase`).
  - `MaugSummarySample`: Maps to `maug_summary_samples` table with fields:
    - `id`, `timestamp_utc`, `lat`, `lon`, `power_v`, `temp_c`,
      `files_count`, `scrubbed_count`, `mic0_type`, `raw_date`, `raw_time`.

**Example usage:**

```python
from app.backend.eti.models import MaugSummarySample
from app.backend.eti.db import SessionLocal

db = SessionLocal()
try:
    sample = MaugSummarySample(lat=1.0, lon=2.0, timestamp_utc=some_datetime)
    db.add(sample)
    db.commit()
finally:
    db.close()
```

### 3. CLI Import Entrypoint (`eti/cli_import.py`)

- Command-line tool to import a MAUG `*_Summary.txt` file into the DB.
- `main()` wires:
  - CLI argument parsing → DB session → `load_summary_file`.

**Run inside the `api` container (recommended):**

```bash
cd /workspaces/HeatmapBat
# ensure stack is up
docker compose up -d --build

# then run import in the api container
docker compose exec api \
  uv run python -m app.backend.eti.cli_import /data/maug/MAUG-1397_A_Summary.txt
```

- Output: `Imported N rows from /data/maug/MAUG-1397_A_Summary.txt`.

### 4. Extract Logic (`eti/extract/summary_import.py`)

- Parses MAUG summary CSV-like text and persists records.
- Functions:
  - `parse_lat_lon(lat_str, ns_str, lon_str, ew_str) -> tuple[float, float]`:
    converts N/S/E/W into signed latitude/longitude.
  - `parse_timestamp(date_str, time_str) -> datetime`:
    parses values like `"2024-May-16"` + `"20:55:59"`.
  - `parse_summary_file(path: Path) -> list[MaugSummarySample]`:
    reads CSV rows and builds `MaugSummarySample` objects.
  - `load_summary_file(db: Session, path: Path) -> int`:
    parses, `db.add_all(samples)`, `db.commit()`, returns count.

**Direct usage example:**

```python
from pathlib import Path
from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file

db = SessionLocal()
try:
    count = load_summary_file(db, Path("/path/to/MAUG-*_Summary.txt"))
    print("Imported", count, "rows")
finally:
    db.close()
```

### 5. Transform / Load Packages

- `eti/extract/__init__.py`: groups extract utilities (currently `summary_import`).
- `eti/load/__init__.py`: placeholder for load-specific utilities.
- `eti/transform/__init__.py`: placeholder for transformation logic.

---

## End-to-End Workflows

### Run Full Stack and Check API

```bash
cd /workspaces/HeatmapBat
docker compose up -d --build
curl http://localhost:8000/health
```

### Import a MAUG Summary File into DB

```bash
cd /workspaces/HeatmapBat
docker compose up -d --build

docker compose exec api \
  uv run python -m app.backend.eti.cli_import /data/maug/MAUG-1397_A_Summary.txt
```

### Develop and Test Backend Locally (No Docker)

```bash
cd /workspaces/HeatmapBat/backend
uv sync
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000
uv run pytest -q
uv run mypy .
```