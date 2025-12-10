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

import json
import logging
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import h3
import pandas as pd
from fastapi import Depends, FastAPI, Query
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.eti.db import get_db
from app.backend.eti.models import MaugSummarySample
from app.backend.eti.s3 import ensure_bucket_exists, get_object_bytes, list_keys

app = FastAPI(title="HeatmapBat API", version="0.1.0")

logger = logging.getLogger(__name__)

_CACHE_TTL = timedelta(minutes=5)
_object_cache: dict[str, tuple[datetime, bytes]] = {}


def _get_cached_bytes(key: str) -> Optional[bytes]:
    now = datetime.now(UTC)
    entry = _object_cache.get(key)
    if not entry:
        return None
    ts, data = entry
    if now - ts > _CACHE_TTL:
        _object_cache.pop(key, None)
        return None
    return data


def _set_cached_bytes(key: str, data: bytes) -> None:
    _object_cache[key] = (datetime.now(UTC), data)


def _resolve_source(point_var: str, hex_var: str) -> tuple[str, str]:
    """Resolve points and H3 sources with a shared override flag.

    Precedence: per-endpoint var -> HEATMAP_SOURCE -> defaults (points=db, h3=local).
    """

    shared = os.environ.get("HEATMAP_SOURCE")
    points_source = os.environ.get(point_var) or shared or "db"
    h3_source = os.environ.get(hex_var) or shared or "local"
    return points_source.lower(), h3_source.lower()


@app.on_event("startup")
def _ensure_bucket_on_startup() -> None:  # pragma: no cover - integration concern
    points_source, h3_source = _resolve_source(
        "HEATMAP_POINTS_SOURCE", "HEATMAP_H3_SOURCE"
    )
    if points_source != "s3" and h3_source != "s3":
        return
    try:
        ensure_bucket_exists()
    except Exception as exc:
        logger.warning("Could not ensure S3 bucket exists: %s", exc)


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
    points_object: str = Query(
        "data/exports/maug_points.geojson",
        description=(
            "When HEATMAP_POINTS_SOURCE=s3, the object key to read points "
            "from. When local, a filesystem path. Supports GeoJSON (.geojson) "
            "or CSV (.csv)."
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

    source, _ = _resolve_source("HEATMAP_POINTS_SOURCE", "HEATMAP_H3_SOURCE")

    if source == "s3":
        cached = _get_cached_bytes(points_object)
        if cached is not None:
            data = cached
        else:
            try:
                data = get_object_bytes(points_object)
            except Exception as exc:  # pragma: no cover - network failure path
                raise HTTPException(
                    status_code=503, detail="Unable to fetch points from object storage"
                ) from exc
            _set_cached_bytes(points_object, data)
        if points_object.endswith(".geojson"):
            payload = json.loads(data)
            features = payload.get("features", [])
            geojson_points: List[HeatmapPoint] = []
            for feat in features:
                coords = feat.get("geometry", {}).get("coordinates", [None, None])
                lon, lat = coords[0], coords[1]
                props = feat.get("properties", {})
                ts = props.get("timestamp_utc")
                if ts is None or lat is None or lon is None:
                    continue
                ts_dt = datetime.fromisoformat(ts)
                if start is not None and ts_dt < start:
                    continue
                if end is not None and ts_dt >= end:
                    continue
                raw = float(props.get("raw_count") or props.get("files_count") or 1)
                geojson_points.append(
                    HeatmapPoint(
                        lat=float(lat),
                        lon=float(lon),
                        raw_count=raw,
                        effort_normalised_weight=raw,
                        timestamp_utc=ts_dt,
                    )
                )
            return geojson_points

        if points_object.endswith(".csv"):
            df = pd.read_csv(BytesIO(data))
            if start is not None:
                df = df[df["timestamp_utc"] >= start.isoformat()]
            if end is not None:
                df = df[df["timestamp_utc"] < end.isoformat()]
            csv_points: List[HeatmapPoint] = []
            for row in df.to_dict(orient="records"):
                ts_str = row.get("timestamp_utc")
                if ts_str is None:
                    continue
                ts_dt = datetime.fromisoformat(str(ts_str))
                raw = float(row.get("raw_count") or row.get("files_count") or 1)
                csv_points.append(
                    HeatmapPoint(
                        lat=float(row["lat"]),
                        lon=float(row["lon"]),
                        raw_count=raw,
                        effort_normalised_weight=raw,
                        timestamp_utc=ts_dt,
                    )
                )
            return csv_points

        raise HTTPException(
            status_code=503,
            detail="Unsupported points object format from object storage",
        )

    stmt = select(MaugSummarySample)
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

    db_points: List[HeatmapPoint] = []
    for row in db.execute(stmt).scalars():
        raw_count = float(row.files_count or 1)
        effort_weight = raw_count
        db_points.append(
            HeatmapPoint(
                lat=row.lat,
                lon=row.lon,
                raw_count=raw_count,
                effort_normalised_weight=effort_weight,
                timestamp_utc=row.timestamp_utc,
            )
        )

    return db_points


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
            "Base directory (or S3 prefix when HEATMAP_H3_SOURCE=s3) "
            "containing partitioned H3 analytics Parquet files."
        ),
    ),
) -> List[H3ParquetCell]:
    """Serve H3 aggregates from precomputed Parquet files.

    When ``HEATMAP_H3_SOURCE=s3``, Parquet partitions are fetched from the
    configured S3/MinIO bucket under ``analytics_dir`` as a prefix. Otherwise
    the local filesystem is used.
    """

    _, source = _resolve_source("HEATMAP_POINTS_SOURCE", "HEATMAP_H3_SOURCE")

    def _load_parquet_frames() -> list[pd.DataFrame]:
        frames: list[pd.DataFrame] = []

        if source == "s3":
            prefix = analytics_dir.rstrip("/") + "/"

            try:
                keys = list(list_keys(prefix))
            except Exception as exc:  # pragma: no cover - network failure path
                raise HTTPException(
                    status_code=503,
                    detail="Unable to list analytics objects from storage",
                ) from exc

            # Discover keys following the naming convention
            # ``h3_analytics_YYYY-MM-DD.parquet``.
            for key in keys:
                name = os.path.basename(key)
                if not name.startswith("h3_analytics_") or not name.endswith(
                    ".parquet"
                ):
                    continue
                try:
                    date_str = name.removeprefix("h3_analytics_").removesuffix(
                        ".parquet"
                    )
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if start is not None and file_date < start:
                    continue
                if end is not None and file_date >= end:
                    continue
                cached = _get_cached_bytes(key)
                if cached is None:
                    try:
                        cached = get_object_bytes(key)
                    except Exception as exc:  # pragma: no cover - network failure path
                        raise HTTPException(
                            status_code=503,
                            detail="Unable to fetch analytics object from storage",
                        ) from exc
                    _set_cached_bytes(key, cached)
                frames.append(pd.read_parquet(BytesIO(cached)))
        else:
            base = Path(analytics_dir)
            if not base.exists():
                return []

            parquet_files = sorted(base.glob("h3_analytics_*.parquet"))
            for path in parquet_files:
                try:
                    date_str = path.stem.split("_")[-1]
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue

                if start is not None and file_date < start:
                    continue
                if end is not None and file_date >= end:
                    continue

                frames.append(pd.read_parquet(path))

        return frames

    frames = _load_parquet_frames()
    if not frames:
        return []

    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return []

    grouped = (
        df.groupby("h3_index", dropna=False)
        .agg(
            raw_count_sum=("raw_count_sum", "sum"),
            sample_count=("sample_count", "sum"),
        )
        .reset_index()
    )

    def _centroid(idx: str) -> tuple[float, float]:
        lat, lon = h3.cell_to_latlng(idx)  # type: ignore[attr-defined]
        return lat, lon

    def _polygon(idx: str) -> list[tuple[float, float]]:
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
