"""FastAPI application entrypoint for HeatmapBat.

Exposes lightweight operations endpoints used by infra and health probes:
- GET /health -> quick readiness/health check used by compose/CI and humans
- GET /live   -> liveness signal (same as health for now)

It also serves a small MapLibre GL based web UI for visualising the
``maug_summary_samples`` data as a heatmap and accompanying JSON APIs that
the map consumes. Two flavours of data are exposed:

* raw point records; and
* H3‑binned aggregates (for smoother, occupancy‑style visuals).
"""

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import h3
import pandas as pd
from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.eti.db import get_db
from app.backend.eti.models import MaugSummarySample

app = FastAPI(title="HeatmapBat API", version="0.1.0")


class HealthResponse(BaseModel):
    status: str = "ok"


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/live", response_model=HealthResponse, tags=["ops"])
def live() -> HealthResponse:
    return HealthResponse(status="ok")


class HeatmapPoint(BaseModel):
    """Single point used by the MapLibre heatmap layer.

    This is intentionally small and front‑end friendly. In addition to the
    basic location and timestamp, it exposes two weighting notions:

    * ``raw_count`` – the underlying detection/count proxy (currently
        ``files_count`` from the summary file).
    * ``effort_normalised_weight`` – a placeholder for an effort‑adjusted
        weight. For now this equals ``raw_count``, but the field is included so
        that future versions can divide by detector effort (e.g. hours
        recording) without changing the API shape.
    """

    lat: float
    lon: float
    # Underlying count proxy from the ETL layer (e.g. number of files).
    raw_count: float
    # Weight that the heatmap should ideally use; currently identical to the
    # raw count, but reserved for effort‑normalised values.
    effort_normalised_weight: float
    timestamp_utc: datetime


@app.get("/api/heatmap/points", response_model=List[HeatmapPoint], tags=["heatmap"])
def get_heatmap_points(
    start: Optional[datetime] = Query(
        None,
        description=(
            "Inclusive lower bound on timestamp_utc. If omitted, all data from "
            "the beginning of the record is included."
        ),
    ),
    end: Optional[datetime] = Query(
        None,
        description=(
            "Exclusive upper bound on timestamp_utc. If omitted, all data up "
            "to the most recent record is included."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[HeatmapPoint]:
    """Return points for the heatmap, optionally filtered by time range.

    The weighting is currently derived from ``files_count`` but this can be
    tweaked later (for example based on power, temperature or a species
    classifier output). A separate ``effort_normalised_weight`` field is
    returned so that future versions can divide by listening effort while
    keeping the API stable.
    """

    stmt = select(MaugSummarySample)
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

    points: List[HeatmapPoint] = []
    for row in db.execute(stmt).scalars():
        # ``files_count`` is used as a simple proxy for activity. If it is
        # missing, fall back to 1 so that the point still appears.
        raw_count = float(row.files_count or 1)

        # Placeholder for effort normalisation. Once effort metrics (e.g.
        # detector hours) are available in the schema, this is the place to
        # divide ``raw_count`` by effort and return the adjusted weight.
        effort_weight = raw_count
        points.append(
            HeatmapPoint(
                lat=row.lat,
                lon=row.lon,
                raw_count=raw_count,
                effort_normalised_weight=effort_weight,
                timestamp_utc=row.timestamp_utc,
            )
        )

    return points


class H3Cell(BaseModel):
    """Aggregated statistics for a single H3 hexagon.

    The ``lat``/``lon`` pair is the centroid of the hex, suitable for
    visualisation as a point, while the counts mirror those used for raw
    points. The actual polygon geometry is not required for the current
    heatmap‑style display.
    """

    h3_index: str
    lat: float
    lon: float
    raw_count: float
    effort_normalised_weight: float


class H3ParquetCell(BaseModel):
    """Aggregated statistics for a single H3 hexagon from Parquet.

    This mirrors :class:`H3Cell` but does not recompute aggregates from the
    raw database on each request. Instead it reads precomputed analytics from
    the ETI transform stage written as Parquet files.
    """

    h3_index: str
    lat: float
    lon: float
    # Aggregated counts as produced by the analytics layer.
    raw_count_sum: float
    sample_count: int
    # Hex boundary as [lon, lat] pairs (single ring) for GeoJSON.
    polygon: list[tuple[float, float]]


@app.get("/api/heatmap/h3", response_model=List[H3Cell], tags=["heatmap"])
def get_heatmap_h3(
    start: Optional[datetime] = Query(
        None,
        description=(
            "Inclusive lower bound on timestamp_utc. If omitted, all data from "
            "the beginning of the record is included."
        ),
    ),
    end: Optional[datetime] = Query(
        None,
        description=(
            "Exclusive upper bound on timestamp_utc. If omitted, all data up "
            "to the most recent record is included."
        ),
    ),
    resolution: int = Query(
        7,
        ge=0,
        le=15,
        description=(
            "H3 resolution to use for binning. Higher values give smaller "
            "hexes; 6–8 is a reasonable range for country‑scale maps."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[H3Cell]:
    """Return H3‑binned aggregates for the heatmap.

    This endpoint mirrors :func:`get_heatmap_points` but aggregates samples
    into H3 cells server‑side so the client deals with far fewer features.
    For now it uses the same ``raw_count`` proxy and passes it through as the
    effort‑normalised weight, ready for future effort‑aware adjustments.
    """

    try:
        import h3
    except ImportError as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(
            "The 'h3' package is required for /api/heatmap/h3. "
            "Install it in the backend environment."
        ) from exc

    stmt = select(MaugSummarySample)
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

    buckets: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"raw_count": 0.0, "lat_sum": 0.0, "lon_sum": 0.0, "n": 0.0}
    )

    for row in db.execute(stmt).scalars():
        # Same raw proxy as for individual points.
        raw = float(row.files_count or 1)
        idx = h3.latlng_to_cell(row.lat, row.lon, resolution)

        bucket = buckets[idx]
        bucket["raw_count"] += raw
        bucket["lat_sum"] += row.lat
        bucket["lon_sum"] += row.lon
        bucket["n"] += 1.0

    cells: List[H3Cell] = []
    for idx, agg in buckets.items():
        if agg["n"] == 0:
            continue
        lat = agg["lat_sum"] / agg["n"]
        lon = agg["lon_sum"] / agg["n"]
        raw_count = agg["raw_count"]
        effort_weight = raw_count

        cells.append(
            H3Cell(
                h3_index=idx,
                lat=lat,
                lon=lon,
                raw_count=raw_count,
                effort_normalised_weight=effort_weight,
            )
        )

    return cells


@app.get(
    "/api/heatmap/h3_parquet", response_model=List[H3ParquetCell], tags=["heatmap"]
)
def get_heatmap_h3_parquet(
    start: Optional[date] = Query(
        None,
        description=(
            "Inclusive lower bound on date (YYYY-MM-DD) for which analytics "
            "should be loaded. If omitted, all available dates are included."
        ),
    ),
    end: Optional[date] = Query(
        None,
        description=(
            "Exclusive upper bound on date (YYYY-MM-DD). If omitted, all "
            "available dates up to the latest are included."
        ),
    ),
    analytics_dir: str = Query(
        "data/analytics/h3_daily",
        description=(
            "Base directory containing partitioned H3 analytics Parquet "
            "files, typically produced by the ETI transform stage."
        ),
    ),
) -> List[H3ParquetCell]:
    """Serve H3 aggregates from precomputed Parquet files.

    This endpoint avoids running H3 binning and aggregation against the live
    database on every request. Instead it assumes that a separate ETI
    transform has produced daily Parquet files in ``analytics_dir`` with at
    least the following columns:

    * ``time_bin_start`` – timestamp at the start of the time window,
    * ``h3_index`` – H3 cell index, and
    * ``raw_count_sum`` / ``sample_count`` – aggregated metrics.
    """

    base = Path(analytics_dir)
    if not base.exists():
        return []

    # Discover candidate files matching the naming convention used by the
    # transform layer: ``h3_analytics_YYYY-MM-DD.parquet``.
    parquet_files = sorted(base.glob("h3_analytics_*.parquet"))
    if not parquet_files:
        return []

    selected: List[Path] = []
    for path in parquet_files:
        # Extract the date component from the filename.
        try:
            date_str = path.stem.split("_")[-1]
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            # Ignore files that do not follow the expected pattern.
            continue

        if start is not None and file_date < start:
            continue
        if end is not None and file_date >= end:
            continue

        selected.append(path)

    if not selected:
        return []

    # Read and concatenate all selected Parquet partitions.
    frames: List[pd.DataFrame] = []
    for path in selected:
        frames.append(pd.read_parquet(path))

    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return []

    # Defensive: aggregate again in case multiple partitions overlap.
    grouped = (
        df.groupby("h3_index", dropna=False)
        .agg(
            raw_count_sum=("raw_count_sum", "sum"),
            sample_count=("sample_count", "sum"),
        )
        .reset_index()
    )

    # Attach centroids and polygon boundaries server-side so the frontend
    # does not need H3 math.
    def _centroid(idx: str) -> tuple[float, float]:
        # v4: returns (lat, lon).
        lat, lon = h3.cell_to_latlng(idx)  # type: ignore[attr-defined]
        return lat, lon

    def _polygon(idx: str) -> list[tuple[float, float]]:
        # v4: returns list of (lat, lon) tuples; convert to (lon, lat).
        boundary = h3.cell_to_boundary(idx)  # type: ignore[attr-defined]
        return [(lon, lat) for lat, lon in boundary]

    grouped[["lat", "lon"]] = grouped["h3_index"].apply(  # type: ignore[assignment]
        lambda idx: pd.Series(_centroid(idx)),
    )
    grouped["polygon"] = grouped["h3_index"].apply(_polygon)

    cells: List[H3ParquetCell] = []
    for row in grouped.to_dict(orient="records"):
        cells.append(
            H3ParquetCell(
                h3_index=row["h3_index"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                raw_count_sum=float(row["raw_count_sum"]),
                sample_count=int(row["sample_count"]),
                polygon=list(row["polygon"]),
            )
        )

    return cells


@app.get("/", response_class=HTMLResponse, tags=["ui"])
def index() -> Any:
    """Serve the main web UI shell.

    The HTML document pulls in its JavaScript and CSS from ``/static`` and
    talks to the ``/api/heatmap/points`` endpoint defined above.
    """

    with open("app/static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# Mount ``/static`` so frontend assets can be served by FastAPI.
app.mount("/static", StaticFiles(directory="app/static"), name="static")
