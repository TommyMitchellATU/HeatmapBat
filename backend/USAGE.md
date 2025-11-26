# HeatmapBat Backend – Current Usage Cheat Sheet

This file focuses only on **functionality that exists and works today**:
health-checked FastAPI API, ETL import of a MAUG summary file into Postgres,
and basic inspection via SQL or Python.

---

## 1. Run the full stack (API + DB + Redis + MinIO)

From the repo root:

```bash
cd /workspaces/HeatmapBat
docker compose up -d --build
```

To stop:

```bash
docker compose down
```

Health check for the API:

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok"}
```

---

## 2. Backend project commands (`backend/`)

Run these from the `backend/` folder:

```bash
cd /workspaces/HeatmapBat/backend

uv sync                      # install dependencies
uv run pytest -q             # run tests
uv run ruff check .          # lint
uv run ruff format --check   # format check
uv run mypy .                # type-check
```

Run the FastAPI dev server directly (without Docker):

```bash
cd /workspaces/HeatmapBat/backend
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000
```

```bash
curl http://localhost:8000/health
```

---

## 3. ETI subsystem – importing a MAUG summary file

ETI lives under `backend/app/backend/eti/` and currently supports
importing a MAUG `*_Summary.txt` file to PostGRIS

### 3.1 Run the CLI importer (inside `api` container)

```bash
cd /workspaces/HeatmapBat
docker compose up -d --build   # ensure stack is running

docker compose exec api \
  uv run python -m app.backend.eti.cli_import /data/maug/MAUG-1397_A_Summary.txt
```

What this does:

- Reads the MAUG summary text file from `/data/maug/MAUG-1397_A_Summary.txt`
  (mounted into the `api` container).
- Parses it via `app.backend.eti.extract.summary_import`.
- Uses `SessionLocal` from `app.backend.eti.db` and the
  `MaugSummarySample` model to insert rows into Postgres.
- Prints `Imported N rows from ...` on success.

### 3.2 Direct Python usage (from a REPL inside `api` container)

```bash
cd /workspaces/HeatmapBat
docker compose up -d --build
docker compose exec api uv run python
```

In the Python prompt:

```python
from pathlib import Path
from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file

db = SessionLocal()
try:
    count = load_summary_file(db, Path("/data/maug/MAUG-1397_A_Summary.txt"))
    print("Imported", count, "rows")
finally:
    db.close()
```

---

## 4. Inspecting the database

The stack runs Postgres with PostGIS as the `db` service.

### 4.1 Inspect via SQL (`psql`)

```bash
cd /workspaces/HeatmapBat
docker compose up -d --build
docker compose exec db psql -U app -d app
```

Inside `psql`:

```sql
\dt;                               -- list tables
SELECT * FROM maug_summary_samples LIMIT 10;  -- peek data
```

Exit with:

```sql
\q
```

### 4.2 Inspect via Python (inside `api` container)

```bash
cd /workspaces/HeatmapBat
docker compose up -d --build
docker compose exec api uv run python
```

In the Python prompt:

```python
from app.backend.eti.db import SessionLocal
from app.backend.eti.models import MaugSummarySample

db = SessionLocal()
try:
    rows = db.query(MaugSummarySample).limit(5).all()
    for row in rows:
        print(row.id, row.timestamp_utc, row.lat, row.lon)
finally:
    db.close()
```

---

## 5. Tests and examples

- API tests live in `backend/app/backend/tests/test_health.py` and call the
  FastAPI app in-process.

Run the whole test suite:

```bash
cd /workspaces/HeatmapBat/backend
uv run pytest -q
```

Run only the health test:

```bash
cd /workspaces/HeatmapBat/backend
uv run pytest app/backend/tests/test_health.py -q
```

---

## Glossary

- **FastAPI (`backend/app/main.py`)** – serves `/health` and `/live`.
- **ETI (`backend/app/backend/eti/`)** – imports MAUG summary files into
  Postgres via `cli_import` and `summary_import`.
- **Postgres/PostGIS (`db` service)** – stores `maug_summary_samples` table.
- **MinIO (`minio` service)** – S3-compatible object store for future
  file-based inputs/outputs (not required for the basic import workflow).

