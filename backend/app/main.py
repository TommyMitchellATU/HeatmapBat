"""FastAPI application entrypoint for HeatmapBat.

# ARCHITECTURE


This file is the single entry point for the HeatmapBat API. It handles:

1. HEALTH CHECKS
   - /health, /live endpoints for Docker/Kubernetes probes

2. DATA SOURCE RESOLUTION
   - Environment flags control whether data comes from DB, local files, or S3
   - HEATMAP_SOURCE, HEATMAP_POINTS_SOURCE, HEATMAP_H3_SOURCE

3. CACHING LAYER
   - In-memory cache with 5-minute TTL for S3 objects
   - Reduces latency and API calls to MinIO/S3

4. HEATMAP ENDPOINTS
   - /api/heatmap/points  → Raw sample points (for point-based heatmaps)
   - /api/heatmap/h3      → Real-time H3 aggregation from DB
   - /api/heatmap/h3_parquet → Pre-computed H3 from Parquet files

5. TIMELINE ENDPOINT
   - /api/timeline/dates  → Per-day sample counts for timeline UI

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
#   - "local" : Read from local filesystem (default for H3 parquet)
#   - "s3"    : Fetch from MinIO/S3 bucket
#
# Environment variables control which source is used:
#   HEATMAP_SOURCE         - Shared override for both endpoints
#   HEATMAP_POINTS_SOURCE  - Override for /api/heatmap/points only
#   HEATMAP_H3_SOURCE      - Override for /api/heatmap/h3_parquet only


def _resolve_source(point_var: str, hex_var: str) -> tuple[str, str]:
    """Determine data sources for points and H3 endpoints.
    
    Resolution order (first non-empty wins):
      1. Per-endpoint env var (HEATMAP_POINTS_SOURCE or HEATMAP_H3_SOURCE)
      2. Shared env var (HEATMAP_SOURCE)
      3. Defaults: points="db", h3="local"
    
    Returns:
        Tuple of (points_source, h3_source), each one of "db", "local", "s3"
    """
    shared = os.environ.get("HEATMAP_SOURCE")
    points_source = os.environ.get(point_var) or shared or "db"
    h3_source = os.environ.get(hex_var) or shared or "local"
    return points_source.lower(), h3_source.lower()


# STARTUP EVENT
# When the server starts, ensure the S3 bucket exists (if S3 mode is enabled).
# This is a no-op if the bucket already exists.

@app.on_event("startup")
def _ensure_bucket_on_startup() -> None:  # pragma: no cover
    """Create S3 bucket on startup if S3 mode is enabled."""
    points_source, h3_source = _resolve_source(
        "HEATMAP_POINTS_SOURCE", "HEATMAP_H3_SOURCE"
    )
    if points_source != "s3" and h3_source != "s3":
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
    source, _ = _resolve_source("HEATMAP_POINTS_SOURCE", "HEATMAP_H3_SOURCE")

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
                    status_code=503, detail="Unable to fetch points from object storage"
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
# This is what the timeline UI uses - fewer features than raw points.
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


class H3ParquetCell(BaseModel):
    """H3 cell loaded from pre-computed Parquet analytics.
    
    Similar to H3Cell but includes polygon boundary for rendering,
    and uses different field names matching the ETL output.
    
    Attributes:
        h3_index: H3 cell identifier
        lat, lon: Cell centroid (from h3.cell_to_latlng)
        raw_count_sum: Total detections across all days loaded
        sample_count: Number of individual samples aggregated
        polygon: List of [lon, lat] pairs forming the hex boundary
    """

    h3_index: str
    lat: float
    lon: float
    raw_count_sum: float
    sample_count: int
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


# ENDPOINT: /api/heatmap/h3_parquet
# Returns H3 aggregates from pre-computed Parquet files (faster for large data).
# The ETL pipeline generates these files: one per day, stored locally or in S3.


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
    """Serve H3 aggregates from pre-computed Parquet files.
    
    Unlike /api/heatmap/h3, this endpoint reads from pre-computed analytics
    rather than aggregating on every request. This is much faster for large
    datasets and enables offline/batch processing pipelines.
    
    File naming convention: h3_analytics_YYYY-MM-DD.parquet
    Location: data/analytics/h3_daily/ (local) or S3 bucket prefix
    
    Data source selection:
        1. HEATMAP_H3_SOURCE env var (db, local, s3)
        2. Falls back to HEATMAP_SOURCE
        3. Default: local filesystem
    
    Response includes hex polygon boundaries for client-side rendering.
    """

    # Determine data source (local filesystem or S3)
    _, source = _resolve_source("HEATMAP_POINTS_SOURCE", "HEATMAP_H3_SOURCE")

    # HELPER: Load Parquet frames from local or S3
    def _load_parquet_frames() -> list[pd.DataFrame]:
        frames: list[pd.DataFrame] = []

        # SOURCE: S3 (MinIO) - List and fetch Parquet files by date prefix
        if source == "s3":
            prefix = analytics_dir.rstrip("/") + "/"

            try:
                keys = list(list_keys(prefix))
            except Exception as exc:  # pragma: no cover - network failure path
                raise HTTPException(
                    status_code=503,
                    detail="Unable to list analytics objects from storage",
                ) from exc

            # Parse filenames to extract dates and filter by time range
            # Expected format: h3_analytics_YYYY-MM-DD.parquet
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
                    
                # Apply date range filters
                if start is not None and file_date < start:
                    continue
                if end is not None and file_date >= end:
                    continue
                    
                # Check cache first, then fetch from S3
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
                
        # SOURCE: Local filesystem (default)
        else:
            base = Path(analytics_dir)
            if not base.exists():
                return []

            # Glob for Parquet files and filter by date
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

    # STEP 1: Load all matching Parquet files
    frames = _load_parquet_frames()
    if not frames:
        return []

    # STEP 2: Concatenate and re-aggregate across dates
    # Multiple days may have the same H3 cell - sum them together
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

    # STEP 3: Add geometry (centroid + polygon boundary)
    def _centroid(idx: str) -> tuple[float, float]:
        """Get the center point of an H3 cell."""
        lat, lon = h3.cell_to_latlng(idx)  # type: ignore[attr-defined]
        return lat, lon

    def _polygon(idx: str) -> list[tuple[float, float]]:
        """Get the hex boundary as [lon, lat] pairs for GeoJSON."""
        boundary = h3.cell_to_boundary(idx)  # type: ignore[attr-defined]
        # h3 returns (lat, lon) but GeoJSON needs (lon, lat)
        # h3 returns (lat, lon) but GeoJSON needs (lon, lat)
        return [(lon, lat) for lat, lon in boundary]

    # Apply geometry functions to each H3 cell
    grouped[["lat", "lon"]] = grouped["h3_index"].apply(  # type: ignore[assignment]
        lambda idx: pd.Series(_centroid(idx)),
    )
    grouped["polygon"] = grouped["h3_index"].apply(_polygon)

    # STEP 4: Build response with full cell data
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


# ENDPOINT: /api/timeline/dates
# Provides date-level statistics for the timeline UI component.
# The frontend uses this to render a sparkline showing activity over time.


class TimelineDateEntry(BaseModel):
    """A single date entry for the timeline sparkline.
    
    Attributes:
        date: Calendar date (YYYY-MM-DD)
        sample_count: Number of distinct sample records on this date
        total_detections: Sum of all detection counts for this date
    """

    date: date
    sample_count: int
    total_detections: int


class TimelineResponse(BaseModel):
    """Response wrapper for timeline data.
    
    Includes the full list of dates plus convenience fields for
    the min/max range (useful for initializing slider bounds).
    """

    dates: List[TimelineDateEntry]
    min_date: Optional[date]
    max_date: Optional[date]


@app.get("/api/timeline/dates", response_model=TimelineResponse, tags=["timeline"])
def get_timeline_dates(
    db: Session = Depends(get_db),
) -> TimelineResponse:
    """Return per-day statistics for the timeline slider.
    
    Aggregates all samples by date to show:
    - How many distinct sample records exist per day
    - Total detection count per day (sum of files_count)
    
    The frontend uses this to:
    1. Set the date range slider bounds (min_date → max_date)
    2. Display an activity sparkline (sample_count per day)
    3. Enable users to filter the heatmap by date range
    
    Performance: Single SQL GROUP BY query, executed fresh each time.
    For large datasets, consider caching or pre-aggregating.
    """
    from sqlalchemy import cast, Date, func

    # SQL: Group samples by date and count
    stmt = (
        select(
            cast(MaugSummarySample.timestamp_utc, Date).label("date"),
            func.count().label("sample_count"),
            func.coalesce(func.sum(MaugSummarySample.files_count), 0).label(
                "total_detections"
            ),
        )
        .group_by(cast(MaugSummarySample.timestamp_utc, Date))
        .order_by(cast(MaugSummarySample.timestamp_utc, Date))
    )

    results = db.execute(stmt).all()

    # Handle empty database
    if not results:
        return TimelineResponse(dates=[], min_date=None, max_date=None)

    # Convert rows to response model
    dates = [
        TimelineDateEntry(
            date=row.date,
            sample_count=row.sample_count,
            total_detections=row.total_detections,
        )
        for row in results
    ]

    return TimelineResponse(
        dates=dates,
        min_date=dates[0].date,  # Already sorted, so first is min
        max_date=dates[-1].date,  # Last is max
    )


# STATIC FILE SERVING
# The frontend is a single-page app (SPA) that loads from index.html and
# makes API calls to the endpoints above. Static assets are served here.


@app.get("/", response_class=HTMLResponse, tags=["ui"])
def index() -> Any:
    """Serve the main web UI shell.
    
    Returns the index.html file which bootstraps the MapLibre-based
    frontend application. The HTML loads JavaScript that:
    1. Fetches /api/timeline/dates for the date slider
    2. Fetches /api/heatmap/points for raw data
    3. Re-aggregates client-side using h3-js for smooth zoom
    """

    with open("app/static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# Mount /static directory for CSS, JS, and other frontend assets
# This makes files at app/static/foo.js available at /static/foo.js
app.mount("/static", StaticFiles(directory="app/static"), name="static")
