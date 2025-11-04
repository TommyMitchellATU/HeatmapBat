# HeatmapBat backend (FastAPI + ETL)

## Run locally (Docker)
```bash
docker compose up -d --build
# API
curl http://localhost:8000/health
# Postgres
docker compose exec db psql -U app -d app -c "SELECT PostGIS_Full_Version();"
# MinIO console: http://localhost:9001 (minioadmin/minioadmin)
```

## Tests & lint
```bash
docker compose exec api uv run pytest -q
pre-commit run --all-files
```

## ETL (placeholder)
```bash
docker compose exec api uv run python -c "from etl.pipeline import run_etl; import pathlib; run_etl(pathlib.Path('/data/in'), pathlib.Path('/data/out'))"
```
