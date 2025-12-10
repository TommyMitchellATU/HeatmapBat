# HeatmapBat Backend – Usage

## Quick commands
```bash
cd /workspaces/HeatmapBat
docker compose up -d --build          # start API+DB+Redis+MinIO
curl http://localhost:8000/health     # readiness
xdg-open http://localhost:8000/ 2>/dev/null || open http://localhost:8000/ || "$BROWSER" http://localhost:8000/  # UI
docker compose logs -f api            # tail API
docker compose exec api uv run pytest -q   # tests in container
docker compose down                   # stop stack
```

---

## Backend dev commands (from `backend/`)
```bash
cd /workspaces/HeatmapBat/backend
uv sync
uv run ruff check .
uv run ruff format --check
uv run mypy .
uv run pytest -q
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000
curl http://localhost:8000/health
```

---

## Map UI + APIs
- UI served at `/` (MapLibre). Data endpoints:
  - `GET /api/heatmap/points?start&end&points_object=`
  - `GET /api/heatmap/h3?resolution=7&start&end`
  - `GET /api/heatmap/h3_parquet?start=YYYY-MM-DD&end=YYYY-MM-DD&analytics_dir=`
- Health: `GET /health`, Liveness: `GET /live`.

---

## ETI: import MAUG summaries
- Import one file (inside `api` container):
```bash
docker compose exec api uv run python -m app.backend.eti.cli_import /data/MAUG-1397_A_Summary.txt
```
- Import all `*_Summary.txt` in `data/`:
```bash
docker compose exec -T api uv run python - << 'PY'
from pathlib import Path
from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file
db = SessionLocal()
try:
    for path in Path("/data").glob("*_Summary.txt"):
        print(f"Imported {load_summary_file(db, path)} rows from {path}")
finally:
    db.close()
PY
```

---

## Export data
- CSV (date-filtered):
```bash
docker compose exec api uv run python -m app.backend.eti.load.cli_export \
  --start "2024-05-16" --end "2024-05-17" /data/exports/maug_points_2024-05-16.csv
```
- GeoJSON (date-filtered):
```bash
docker compose exec api uv run python -m app.backend.eti.load.cli_geojson_export \
  --start "2024-05-16" --end "2024-05-17" /data/exports/maug_points_2024-05-16.geojson
```
- Raw DB → CSV via psql:
```bash
docker compose exec db psql -U app -d app -c \
  "COPY maug_summary_samples TO STDOUT WITH CSV HEADER" > data/maug_summary_samples_combined.csv
```
- Upload combined CSV to MinIO:
```bash
docker compose exec api uv run python -m app.backend.eti.export_to_s3
```

---

## MinIO (S3-compatible)
- Console: http://localhost:9001
- API endpoint: http://localhost:9000
- Default creds: `minioadmin` / `minioadmin`
- Bucket: `heatmapbat` (auto-created on startup when S3 mode is enabled)
- Switch to S3 mode: set `HEATMAP_SOURCE=s3` (or per-endpoint `HEATMAP_POINTS_SOURCE` / `HEATMAP_H3_SOURCE`).

---

## Inspect the database
- SQL shell:
```bash
docker compose exec db psql -U app -d app
```
- Python shell (inside api):
```bash
docker compose exec api uv run python
```
Then:
```python
from app.backend.eti.db import SessionLocal
from app.backend.eti.models import MaugSummarySample
db = SessionLocal()
try:
    print(db.query(MaugSummarySample).limit(5).all())
finally:
    db.close()
```

---

## Tests
- In container: `docker compose exec api uv run pytest -q`
- Locally from `backend/`: `uv run pytest -q`