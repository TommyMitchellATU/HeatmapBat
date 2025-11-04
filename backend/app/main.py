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