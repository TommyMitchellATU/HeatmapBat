"""FastAPI application entrypoint for HeatmapBat.

# ARCHITECTURE


This file is the single entry point for the HeatmapBat API. It handles:

1. HEALTH CHECKS
   - /health, /live endpoints for Docker/Kubernetes probes

2. DATA SOURCE RESOLUTION
   - Environment flags control whether data comes from DB, local files, or S3
   - HEATMAP_SOURCE, HEATMAP_POINTS_SOURCE, HEATMAP_ACTIVITY_SOURCE,
     HEATMAP_PREDICTIONS_SOURCE

3. CACHING LAYER
   - In-memory cache with 5-minute TTL for S3 objects
   - Reduces latency and API calls to MinIO/S3

4. HEATMAP ENDPOINTS
   - /api/heatmap/points  → Raw sample points (for point-based heatmaps)
   - /api/heatmap/h3      → Real-time H3 aggregation from DB

5b. OCCUPANCY ENDPOINTS (model vs reality)
   - /api/occupancy/predictions → Observed vs M6-predicted activity per hex-night
   - /api/occupancy/nights      → Nights with predictions, for the timeline

5. ACTIVITY ENDPOINTS (the activity map, from detector_nightly Parquet)
   - /api/activity/hexes          → Per-hex totals over a night range
   - /api/activity/nights         → Per-night totals for the timeline
   - /api/activity/survey_windows → First/last night per hex (survey trail)

6. STATIC FILE SERVING
   - /        → Main HTML page (MapLibre map)
   - /static  → CSS, JS assets


# DATA FLOW: Browser Request → JSON Response

Browser: GET /api/heatmap/h3?start=2024-05-16&end=2024-05-17&resolution=7
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. FastAPI receives request, parses query params via Pydantic          │
│    - start: datetime = 2024-05-16T00:00:00                             │
│    - end: datetime = 2024-05-17T00:00:00                               │
│    - resolution: int = 7                                                │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. Database query via SQLAlchemy                                        │
│    SELECT * FROM maug_summary_samples                                   │
│    WHERE timestamp_utc >= '2024-05-16' AND timestamp_utc < '2024-05-17' │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. H3 spatial binning (in-memory aggregation)                           │
│    For each sample row:                                                 │
│      - h3.latlng_to_cell(lat, lon, resolution=7) → "872830828ffffff"   │
│      - Accumulate raw_count into buckets[h3_index]                     │
│      - Track lat/lon sums for centroid calculation                      │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. Build response using Pydantic models                                 │
│    H3Cell(h3_index="872830828ffffff", lat=53.5, lon=-7.2,              │
│           raw_count=42, effort_normalised_weight=42)                   │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. FastAPI serializes to JSON and returns                               │
│    [{"h3_index": "872830828ffffff", "lat": 53.5, "lon": -7.2, ...}]    │
└─────────────────────────────────────────────────────────────────────────┘

"""

import json
import logging
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import h3
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.eti.db import get_db
from app.backend.eti.models import MaugSummarySample
from app.backend.eti.s3 import ensure_bucket_exists, get_object_bytes, list_keys


# APPLICATION SETUP
app = FastAPI(title="HeatmapBat API", version="0.1.0")

logger = logging.getLogger(__name__)


# CACHING LAYER
# Simple in-memory cache for S3 objects to avoid repeated network calls.
# Each entry stores (timestamp, bytes) and expires after 5 minutes.
# This is process-local and will reset on server restart.

_CACHE_TTL = timedelta(minutes=5)
_object_cache: dict[str, tuple[datetime, bytes]] = {}


def _get_cached_bytes(key: str) -> Optional[bytes]:
    """Retrieve cached S3 object bytes if not expired."""
    now = datetime.now(UTC)
    entry = _object_cache.get(key)
    if not entry:
        return None
    ts, data = entry
    if now - ts > _CACHE_TTL:
        # Cache entry expired, remove it
        _object_cache.pop(key, None)
        return None
    return data


def _set_cached_bytes(key: str, data: bytes) -> None:
    """Store S3 object bytes in cache with current timestamp."""
    _object_cache[key] = (datetime.now(UTC), data)


# DATA SOURCE RESOLUTION
# The API can read data from three sources:
#   - "db"    : Query PostgreSQL/PostGIS directly (default for points)
#   - "local" : Read from local filesystem (default for the Parquet routes)
#   - "s3"    : Fetch from MinIO/S3 bucket
#
# Environment variables control which source is used:
#   HEATMAP_SOURCE             - Shared override for every route below
#   HEATMAP_POINTS_SOURCE      - Override for /api/heatmap/points only
#   HEATMAP_ACTIVITY_SOURCE    - Override for /api/activity/* only
#   HEATMAP_PREDICTIONS_SOURCE - Override for /api/occupancy/* only


def _resolve_points_source() -> str:
    """Source for /api/heatmap/points: HEATMAP_POINTS_SOURCE, then HEATMAP_SOURCE, then "db"."""
    source = (
        os.environ.get("HEATMAP_POINTS_SOURCE")
        or os.environ.get("HEATMAP_SOURCE")
        or "db"
    )
    return source.lower()


def _resolve_predictions_source() -> str:
    """Source for /api/occupancy/*: HEATMAP_PREDICTIONS_SOURCE, then HEATMAP_SOURCE, then "local"."""
    source = (
        os.environ.get("HEATMAP_PREDICTIONS_SOURCE")
        or os.environ.get("HEATMAP_SOURCE")
        or "local"
    )
    return source.lower()


def _resolve_activity_source() -> str:
    """Source for /api/activity/*: HEATMAP_ACTIVITY_SOURCE, then HEATMAP_SOURCE, then "local"."""
    source = (
        os.environ.get("HEATMAP_ACTIVITY_SOURCE")
        or os.environ.get("HEATMAP_SOURCE")
        or "local"
    )
    return source.lower()


# DAILY PARQUET LOADING
# Shared by /api/activity/* and /api/occupancy/*: one file per date,
# named <file_prefix>YYYY-MM-DD.parquet, in a local directory or an S3 prefix.


def _file_date(name: str, file_prefix: str) -> Optional[date]:
    """Date in a '<file_prefix>YYYY-MM-DD.parquet' filename, or None if it doesn't match."""
    if not name.startswith(file_prefix) or not name.endswith(".parquet"):
        return None
    date_str = name.removeprefix(file_prefix).removesuffix(".parquet")
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def _in_range(file_date: date, start: Optional[date], end: Optional[date]) -> bool:
    """True when start <= file_date < end; a missing bound is open."""
    if start is not None and file_date < start:
        return False
    if end is not None and file_date >= end:
        return False
    return True


def _load_daily_parquet(
    base_dir: str,
    file_prefix: str,
    start: Optional[date],
    end: Optional[date],
    source: str,
) -> list[pd.DataFrame]:
    """Read every <file_prefix>YYYY-MM-DD.parquet in base_dir whose date is in [start, end).

    base_dir is an S3 key prefix when source is "s3" (objects go through the
    5-minute cache), otherwise a local directory; a missing directory gives [].
    """
    frames: list[pd.DataFrame] = []

    if source == "s3":
        try:
            keys = list(list_keys(base_dir.rstrip("/") + "/"))
        except Exception as exc:  # pragma: no cover - network failure path
            raise HTTPException(
                status_code=503,
                detail="Unable to list analytics objects from storage",
            ) from exc

        for key in keys:
            file_date = _file_date(os.path.basename(key), file_prefix)
            if file_date is None or not _in_range(file_date, start, end):
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
        return frames

    base = Path(base_dir)
    if not base.exists():
        return []
    for path in sorted(base.glob(f"{file_prefix}*.parquet")):
        file_date = _file_date(path.name, file_prefix)
        if file_date is None or not _in_range(file_date, start, end):
            continue
        frames.append(pd.read_parquet(path))
    return frames


def _hex_centroid(idx: str) -> tuple[float, float]:
    """(lat, lon) of an H3 cell's centre."""
    lat, lon = h3.cell_to_latlng(idx)  # type: ignore[attr-defined]
    return lat, lon


def _hex_polygon(idx: str) -> list[tuple[float, float]]:
    """H3 cell boundary as [lon, lat] pairs for GeoJSON (h3 returns lat, lon)."""
    boundary = h3.cell_to_boundary(idx)  # type: ignore[attr-defined]
    return [(lon, lat) for lat, lon in boundary]


# STARTUP EVENT
# When the server starts, ensure the S3 bucket exists (if S3 mode is enabled).
# This is a no-op if the bucket already exists.


@app.on_event("startup")
def _ensure_bucket_on_startup() -> None:  # pragma: no cover
    """Create S3 bucket on startup if S3 mode is enabled."""
    sources = (
        _resolve_points_source(),
        _resolve_activity_source(),
        _resolve_predictions_source(),
    )
    if "s3" not in sources:
        return  # S3 not used, skip bucket check
    try:
        ensure_bucket_exists()
    except Exception as exc:
        logger.warning("Could not ensure S3 bucket exists: %s", exc)


# HEALTH CHECK ENDPOINTS
# Used by Docker Compose, Kubernetes, and CI to verify the service is running.
# These are intentionally lightweight and don't hit the database.


class HealthResponse(BaseModel):
    """Response model for health check endpoints."""

    status: str = "ok"


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Readiness probe - returns 200 if the API is ready to serve requests."""
    return HealthResponse(status="ok")


@app.get("/live", response_model=HealthResponse, tags=["ops"])
def live() -> HealthResponse:
    """Liveness probe - returns 200 if the process is alive."""
    return HealthResponse(status="ok")


# PYDANTIC MODELS FOR API RESPONSES
# These models define the JSON structure returned by API endpoints.
# FastAPI automatically validates and serializes responses using these.


class HeatmapPoint(BaseModel):
    """Single detection point for the MapLibre heatmap layer.

    Represents one sample from the database, transformed for frontend use.

    Attributes:
        lat, lon: Geographic coordinates (WGS84)
        raw_count: Detection count from the detector (files_count field)
        effort_normalised_weight: Reserved for future effort-adjusted weighting
                                  (currently equals raw_count)
        timestamp_utc: When the sample was recorded

    Example JSON output:
        {"lat": 53.5, "lon": -7.2, "raw_count": 5,
         "effort_normalised_weight": 5, "timestamp_utc": "2024-05-16T21:00:00"}
    """

    lat: float
    lon: float
    raw_count: float
    effort_normalised_weight: float
    timestamp_utc: datetime


# ENDPOINT: /api/heatmap/points
# Returns raw sample points for point-based heatmap visualization.
# The frontend can render these directly as a heatmap layer.
#
# Data sources (controlled by HEATMAP_POINTS_SOURCE):
#   - "db": Query maug_summary_samples table directly (default)
#   - "s3": Fetch GeoJSON/CSV from MinIO bucket
#   - "local": Read GeoJSON/CSV from filesystem


@app.get("/api/heatmap/points", response_model=List[HeatmapPoint], tags=["heatmap"])
def get_heatmap_points(
    # Query parameters with OpenAPI documentation
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
    # Database session injected by FastAPI's dependency injection
    db: Session = Depends(get_db),
) -> List[HeatmapPoint]:
    """Return detection points for the heatmap, optionally filtered by time.

    This endpoint serves raw sample data. Each row from maug_summary_samples
    becomes one HeatmapPoint in the response. For large datasets, consider
    using /api/heatmap/h3 instead (aggregates into fewer features).

    Response size: ~100 bytes per point × number of matching samples
    """

    # Determine data source from environment variables
    source = _resolve_points_source()

    # SOURCE: S3/MinIO - Fetch pre-exported GeoJSON or CSV
    if source == "s3":
        # Check cache first to avoid repeated S3 calls
        cached = _get_cached_bytes(points_object)
        if cached is not None:
            data = cached
        else:
            try:
                data = get_object_bytes(points_object)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Unable to fetch points from object storage",
                ) from exc
            _set_cached_bytes(points_object, data)

        # Parse GeoJSON format
        if points_object.endswith(".geojson"):
            payload = json.loads(data)
            features = payload.get("features", [])
            geojson_points: List[HeatmapPoint] = []
            for feat in features:
                # GeoJSON coordinates are [lon, lat] order
                coords = feat.get("geometry", {}).get("coordinates", [None, None])
                lon, lat = coords[0], coords[1]
                props = feat.get("properties", {})
                ts = props.get("timestamp_utc")
                if ts is None or lat is None or lon is None:
                    continue
                ts_dt = datetime.fromisoformat(ts)
                # Apply time range filter
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

        # Parse CSV format
        if points_object.endswith(".csv"):
            df = pd.read_csv(BytesIO(data))
            # Filter by time range using string comparison (ISO format)
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

    # SOURCE: Database (default) - Query PostgreSQL directly
    # Build SQLAlchemy query with optional time filters
    stmt = select(MaugSummarySample)
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

    # Transform each DB row into a HeatmapPoint
    db_points: List[HeatmapPoint] = []
    for row in db.execute(stmt).scalars():
        raw_count = float(row.files_count or 1)
        effort_weight = raw_count  # Placeholder for future effort normalization
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


# ENDPOINT: /api/heatmap/h3
# Returns H3-binned aggregates computed on-the-fly from the database.
# Fewer features than raw points; the map itself uses /api/activity/hexes.
#
# How H3 works:
#   - The world is divided into hexagonal cells at different resolutions
#   - Resolution 0 = huge cells, Resolution 15 = tiny cells
#   - Resolution 7 (default) ≈ 5km² hexagons, good for country-scale maps
#   - Each lat/lon → unique H3 index string like "872830828ffffff"


class H3Cell(BaseModel):
    """Aggregated statistics for a single H3 hexagon.

    Multiple sample points falling within the same hex are summed together.
    The lat/lon is the centroid of contributing points (not the hex center).

    Attributes:
        h3_index: H3 cell identifier (e.g., "872830828ffffff")
        lat, lon: Average position of samples in this cell
        raw_count: Sum of all detection counts in this cell
        effort_normalised_weight: Reserved for future use
    """

    h3_index: str
    lat: float
    lon: float
    raw_count: float
    effort_normalised_weight: float


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
            "hexes; 6–8 is a reasonable range for country-scale maps."
        ),
    ),
    db: Session = Depends(get_db),
) -> List[H3Cell]:
    """Return H3-binned aggregates for the heatmap.

    This endpoint performs real-time aggregation:
    1. Query all samples in the time range from PostgreSQL
    2. Convert each (lat, lon) to an H3 cell index
    3. Sum raw_count values by H3 cell
    4. Return one H3Cell per unique hexagon

    Performance: O(n) where n = samples in time range
    Response size: Much smaller than /points (one entry per hex, not per sample)

    Example flow:
        3 samples at slightly different locations in Dublin →
        All map to same H3 cell "872830828ffffff" →
        Response contains 1 H3Cell with raw_count = sum of all 3
    """

    # Import h3 (should already be imported at module level, but defensive)
    try:
        import h3
    except ImportError as exc:
        raise RuntimeError(
            "The 'h3' package is required for /api/heatmap/h3. "
            "Install it in the backend environment."
        ) from exc

    # STEP 1: Query database for samples in time range
    stmt = select(MaugSummarySample)
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

    # STEP 2: Aggregate samples into H3 buckets
    # Each bucket tracks: total raw_count, sum of lats/lons, sample count
    buckets: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"raw_count": 0.0, "lat_sum": 0.0, "lon_sum": 0.0, "n": 0.0}
    )

    for row in db.execute(stmt).scalars():
        raw = float(row.files_count or 1)
        # Convert lat/lon to H3 index at requested resolution
        idx = h3.latlng_to_cell(row.lat, row.lon, resolution)

        # Accumulate into bucket
        bucket = buckets[idx]
        bucket["raw_count"] += raw
        bucket["lat_sum"] += row.lat
        bucket["lon_sum"] += row.lon
        bucket["n"] += 1.0

    # STEP 3: Build response from aggregated buckets
    cells: List[H3Cell] = []
    for idx, agg in buckets.items():
        if agg["n"] == 0:
            continue
        # Calculate centroid as average of all points in this cell
        lat = agg["lat_sum"] / agg["n"]
        lon = agg["lon_sum"] / agg["n"]
        raw_count = agg["raw_count"]
        effort_weight = raw_count  # Placeholder

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


# ENDPOINTS: /api/activity/hexes, /api/activity/nights, /api/activity/survey_windows
# The activity map, read from the detector_nightly Parquet (one row per night x hex x
# detector serial, noon-to-noon nights) and rolled up per request.

_ACTIVITY_DIR = "/data/analytics/detector_nightly"
_ACTIVITY_PREFIX = "detector_nightly_"


def _nightly_dir_query() -> Any:
    """Shared nightly_dir query parameter for the /api/activity/* routes."""
    return Query(
        _ACTIVITY_DIR,
        description=(
            "Directory (or S3 prefix when HEATMAP_ACTIVITY_SOURCE=s3) holding "
            "detector_nightly_YYYY-MM-DD.parquet files."
        ),
    )


def _load_detector_nightly(
    nightly_dir: str, start: Optional[date] = None, end: Optional[date] = None
) -> pd.DataFrame:
    """Detector-night rows for nights in [start, end), with ``night`` as a date; empty if none."""
    frames = _load_daily_parquet(
        nightly_dir, _ACTIVITY_PREFIX, start, end, _resolve_activity_source()
    )
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["night"] = pd.to_datetime(df["night"]).dt.date
    return df


# RULE: detector_nights is a ROW COUNT, which is only right because detector_nightly has exactly
# one row per (night, h3_index, detector_serial). Summing sample_count or counting serials
# instead would give minutes or detectors, not detector-nights. [doc: Detector-nightly analytics]
_ACTIVITY_SUMS: dict[str, tuple[str, Any]] = {
    "raw_count_sum": ("raw_count_sum", "sum"),
    "sample_count": ("sample_count", "sum"),
    "detector_nights": ("detector_serial", "size"),
}


class ActivityHex(BaseModel):
    """One hex's bat activity summed over the requested nights.

    Attributes:
        h3_index: Site (resolution-10 H3 hex)
        raw_count_sum: Detections (files recorded) over the nights
        sample_count: 1-minute log rows over the nights (recording effort)
        detector_nights: Detector x night surveys summed into raw_count_sum
        n_detectors: Distinct detector serials seen
        detections_per_night: raw_count_sum / detector_nights
        lat, lon: Hex centre
        polygon: Hex boundary as [lon, lat] pairs
    """

    h3_index: str
    raw_count_sum: int
    sample_count: int
    detector_nights: int
    n_detectors: int
    detections_per_night: float
    lat: float
    lon: float
    polygon: list[tuple[float, float]]


class ActivityNight(BaseModel):
    """One night's activity summed over every hex surveyed that night (timeline sparkline)."""

    night: date
    raw_count_sum: int
    sample_count: int
    detector_nights: int
    n_hexes: int


class ActivityNightsResponse(BaseModel):
    """All nights with data, plus the range for the slider bounds."""

    nights: List[ActivityNight]
    min_night: Optional[date]
    max_night: Optional[date]


class ActivitySurveyWindow(BaseModel):
    """The nights one hex was surveyed.

    Assumes one deployment per hex (each res-10 hex is one wind farm x one
    deployment window), so the window is simply its first to last night.

    Attributes:
        h3_index: Site (resolution-10 H3 hex)
        first_night, last_night: First and last night with data (inclusive)
        n_nights: Nights with data inside the window
        raw_count_sum: Detections over the window
        detector_nights: Detector x night surveys over the window
        detections_per_night: raw_count_sum / detector_nights
        lat, lon: Hex centre
    """

    h3_index: str
    first_night: date
    last_night: date
    n_nights: int
    raw_count_sum: int
    detector_nights: int
    detections_per_night: float
    lat: float
    lon: float


@app.get("/api/activity/hexes", response_model=List[ActivityHex], tags=["activity"])
def get_activity_hexes(
    start: Optional[date] = Query(
        None, description="First night (YYYY-MM-DD, inclusive); omit for all."
    ),
    end: Optional[date] = Query(
        None, description="Night after the last one (exclusive); omit for all."
    ),
    nightly_dir: str = _nightly_dir_query(),
) -> List[ActivityHex]:
    """Per-hex activity summed over the nights in [start, end), at resolution 10.

    The map coarsens these to its zoom level client-side. A night runs noon to
    noon and is labelled by its start date.
    """
    df = _load_detector_nightly(nightly_dir, start, end)
    if df.empty:
        return []

    grouped = (
        df.groupby("h3_index")
        .agg(**_ACTIVITY_SUMS, n_detectors=("detector_serial", "nunique"))
        .reset_index()
    )

    cells: List[ActivityHex] = []
    for row in grouped.to_dict(orient="records"):
        lat, lon = _hex_centroid(row["h3_index"])
        nights = int(row["detector_nights"])
        cells.append(
            ActivityHex(
                h3_index=row["h3_index"],
                raw_count_sum=int(row["raw_count_sum"]),
                sample_count=int(row["sample_count"]),
                detector_nights=nights,
                n_detectors=int(row["n_detectors"]),
                detections_per_night=round(row["raw_count_sum"] / max(nights, 1), 2),
                lat=lat,
                lon=lon,
                polygon=_hex_polygon(row["h3_index"]),
            )
        )
    return cells


@app.get(
    "/api/activity/nights",
    response_model=ActivityNightsResponse,
    tags=["activity"],
)
def get_activity_nights(
    nightly_dir: str = _nightly_dir_query(),
) -> ActivityNightsResponse:
    """Every night with data, oldest first, with totals across its hexes.

    The activity timeline uses this for its slider steps and sparkline.
    """
    df = _load_detector_nightly(nightly_dir)
    if df.empty:
        return ActivityNightsResponse(nights=[], min_night=None, max_night=None)

    per_night = (
        df.groupby("night")
        .agg(**_ACTIVITY_SUMS, n_hexes=("h3_index", "nunique"))
        .reset_index()
        .sort_values("night")
    )
    nights = [
        ActivityNight(
            night=row["night"],
            raw_count_sum=int(row["raw_count_sum"]),
            sample_count=int(row["sample_count"]),
            detector_nights=int(row["detector_nights"]),
            n_hexes=int(row["n_hexes"]),
        )
        for row in per_night.to_dict(orient="records")
    ]
    return ActivityNightsResponse(
        nights=nights, min_night=nights[0].night, max_night=nights[-1].night
    )


@app.get(
    "/api/activity/survey_windows",
    response_model=List[ActivitySurveyWindow],
    tags=["activity"],
)
def get_activity_survey_windows(
    nightly_dir: str = _nightly_dir_query(),
) -> List[ActivitySurveyWindow]:
    """Every hex's survey window (first to last night with data), oldest first.

    Drives the activity map's grey trail of finished surveys.
    """
    df = _load_detector_nightly(nightly_dir)
    if df.empty:
        return []

    windows = (
        df.groupby("h3_index")
        .agg(
            **_ACTIVITY_SUMS,
            first_night=("night", "min"),
            last_night=("night", "max"),
            n_nights=("night", "nunique"),
        )
        .reset_index()
        .sort_values(["first_night", "h3_index"])
    )

    rows: List[ActivitySurveyWindow] = []
    for row in windows.to_dict(orient="records"):
        lat, lon = _hex_centroid(row["h3_index"])
        nights = int(row["detector_nights"])
        rows.append(
            ActivitySurveyWindow(
                h3_index=row["h3_index"],
                first_night=row["first_night"],
                last_night=row["last_night"],
                n_nights=int(row["n_nights"]),
                raw_count_sum=int(row["raw_count_sum"]),
                detector_nights=nights,
                detections_per_night=round(row["raw_count_sum"] / max(nights, 1), 2),
                lat=lat,
                lon=lon,
            )
        )
    return rows


# ENDPOINTS: /api/occupancy/predictions, /api/occupancy/nights
# Model vs reality: observed bat activity against leave-one-hex-out M6
# predictions, exported by occupancy_modeling.ipynb section 15.

_PREDICTIONS_DIR = "/data/analytics/occupancy_predictions"
_PREDICTIONS_PREFIX = "occupancy_predictions_"

PredictionKind = Literal["out_of_sample", "forecast"]
"""How a row was predicted; the notebook writes both kinds into the same nightly files."""


class OccupancyPrediction(BaseModel):
    """Observed vs predicted bat activity for one hex on one night.

    Attributes:
        night: Noon-to-noon night, labelled by its start date
        h3_index: Site (resolution-10 H3 hex)
        observed: Summed raw counts over the detectors surveyed that night
        pred_mean, pred_median: Mean / median of the predicted total; the
            median is the typical site and is what the map colour divides by
        pred_lo, pred_hi: Central pred_interval band of the predicted total
        pred_interval: Band width, e.g. 0.9 for the 5th to 95th percentile
        n_detectors: Detector-nights summed into observed and predicted
        prediction_kind: How it was predicted; "out_of_sample" = by a fit
            that never saw this hex, "forecast" = by a fit on nights before
            this hex's survey started
        forecast_from: Last night the forecast fit saw (forecast rows only)
        forecast_sites: Sites in the forecast fit (forecast rows only)
        lat, lon: Hex centre
        polygon: Hex boundary as [lon, lat] pairs
    """

    night: date
    h3_index: str
    observed: int
    pred_mean: float
    pred_median: float
    pred_lo: float
    pred_hi: float
    pred_interval: float
    n_detectors: int
    prediction_kind: str
    forecast_from: Optional[date] = None
    forecast_sites: Optional[int] = None
    lat: float
    lon: float
    polygon: list[tuple[float, float]]


class OccupancyNight(BaseModel):
    """One night that has predictions, summed over its hexes (timeline sparkline).

    pred_median is the sum of the per-hex medians, not the median of the total.
    """

    night: date
    observed: int
    pred_median: float
    n_hexes: int


class OccupancyNightsResponse(BaseModel):
    """All nights with predictions, plus the range for the slider bounds."""

    nights: List[OccupancyNight]
    min_night: Optional[date]
    max_night: Optional[date]


def _load_predictions(
    predictions_dir: str,
    kind: PredictionKind,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.DataFrame:
    """Prediction rows of one kind for nights in [start, end); empty if there are none.

    Files written before forecasts existed have no prediction_kind values other
    than out_of_sample and may lack the forecast columns.
    """
    frames = _load_daily_parquet(
        predictions_dir, _PREDICTIONS_PREFIX, start, end, _resolve_predictions_source()
    )
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    # RULE: filter by prediction_kind before any per-night sum - every kind shares the same
    # nightly files, so /api/occupancy/nights would otherwise add forecasts to the
    # out-of-sample totals and the map would draw two rows per hex-night. [doc: Model vs reality]
    return df[df["prediction_kind"] == kind]


def _optional(value: Any) -> Any:
    """None for a missing Parquet value (NaN, NaT, pd.NA), else the value."""
    return None if pd.isna(value) else value


def _kind_query() -> Any:
    """Shared kind query parameter for the /api/occupancy routes."""
    return Query(
        "out_of_sample",
        description=(
            "Which predictions to serve: out_of_sample (each hex by a fit that "
            "never saw it) or forecast (each survey by a fit on earlier nights)."
        ),
    )


def _predictions_dir_query() -> Any:
    """Shared predictions_dir query parameter for the /api/occupancy routes."""
    return Query(
        _PREDICTIONS_DIR,
        description=(
            "Directory (or S3 prefix when HEATMAP_PREDICTIONS_SOURCE=s3) "
            "containing occupancy_predictions_YYYY-MM-DD.parquet files."
        ),
    )


@app.get(
    "/api/occupancy/predictions",
    response_model=List[OccupancyPrediction],
    tags=["occupancy"],
)
def get_occupancy_predictions(
    start: Optional[date] = Query(
        None, description="Inclusive first night (YYYY-MM-DD). Omit for all."
    ),
    end: Optional[date] = Query(
        None, description="Exclusive last night (YYYY-MM-DD). Omit for all."
    ),
    kind: PredictionKind = _kind_query(),
    predictions_dir: str = _predictions_dir_query(),
) -> List[OccupancyPrediction]:
    """Observed vs M6-predicted activity per hex-night, for nights in [start, end).

    One row per hex per night for the requested kind, with hex geometry for
    rendering. Source: HEATMAP_PREDICTIONS_SOURCE, then HEATMAP_SOURCE, then
    local files.
    """
    df = _load_predictions(predictions_dir, kind, start, end)
    if df.empty:
        return []
    df = df.sort_values(["night", "h3_index"])

    rows: List[OccupancyPrediction] = []
    for row in df.to_dict(orient="records"):
        lat, lon = _hex_centroid(row["h3_index"])
        rows.append(
            OccupancyPrediction(
                night=row["night"],
                h3_index=row["h3_index"],
                observed=int(row["observed"]),
                pred_mean=float(row["pred_mean"]),
                pred_median=float(row["pred_median"]),
                pred_lo=float(row["pred_lo"]),
                pred_hi=float(row["pred_hi"]),
                pred_interval=float(row["pred_interval"]),
                n_detectors=int(row["n_detectors"]),
                prediction_kind=row["prediction_kind"],
                forecast_from=_optional(row.get("forecast_from")),
                forecast_sites=_optional(row.get("forecast_sites")),
                lat=lat,
                lon=lon,
                polygon=_hex_polygon(row["h3_index"]),
            )
        )
    return rows


@app.get(
    "/api/occupancy/nights",
    response_model=OccupancyNightsResponse,
    tags=["occupancy"],
)
def get_occupancy_nights(
    kind: PredictionKind = _kind_query(),
    predictions_dir: str = _predictions_dir_query(),
) -> OccupancyNightsResponse:
    """Every night with predictions of the requested kind, oldest first, with totals across its hexes.

    The model-vs-reality timeline uses this for its nights and sparkline.
    """
    df = _load_predictions(predictions_dir, kind)
    if df.empty:
        return OccupancyNightsResponse(nights=[], min_night=None, max_night=None)

    per_night = (
        df.groupby("night")
        .agg(
            observed=("observed", "sum"),
            pred_median=("pred_median", "sum"),
            n_hexes=("h3_index", "nunique"),
        )
        .reset_index()
        .sort_values("night")
    )
    nights = [
        OccupancyNight(
            night=row["night"],
            observed=int(row["observed"]),
            pred_median=float(row["pred_median"]),
            n_hexes=int(row["n_hexes"]),
        )
        for row in per_night.to_dict(orient="records")
    ]
    return OccupancyNightsResponse(
        nights=nights, min_night=nights[0].night, max_night=nights[-1].night
    )


# STATIC FILE SERVING
# The frontend is a single-page app (SPA) that loads from index.html and
# makes API calls to the endpoints above. Static assets are served here.


@app.get("/", response_class=HTMLResponse, tags=["ui"])
def index() -> Any:
    """Serve the main web UI shell.

    Returns the index.html file which bootstraps the MapLibre-based
    frontend application. The HTML loads JavaScript that:
    1. Fetches /api/activity/nights for the night slider
    2. Fetches /api/activity/hexes for the res-10 hexes in a night range
    3. Re-aggregates client-side using h3-js for smooth zoom
    """

    with open("app/static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# Mount /static directory for CSS, JS, and other frontend assets
# This makes files at app/static/foo.js available at /static/foo.js
app.mount("/static", StaticFiles(directory="app/static"), name="static")
