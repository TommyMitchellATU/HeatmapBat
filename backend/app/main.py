"""FastAPI application entrypoint for HeatmapBat.

Exposes lightweight operations endpoints used by infra and health probes:
- GET /health -> quick readiness/health check used by compose/CI and humans
- GET /live   -> liveness signal (same as health for now)

- Keeps the app minimal while the ETL and data models evolve.
- Demonstrates the project’s testing pattern and response modelling (Pydantic v2).
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="HeatmapBat API", version="0.1.0")


class HealthResponse(BaseModel):
    status: str = "ok"


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/live", response_model=HealthResponse, tags=["ops"])
def live() -> HealthResponse:
    return HealthResponse(status="ok")
