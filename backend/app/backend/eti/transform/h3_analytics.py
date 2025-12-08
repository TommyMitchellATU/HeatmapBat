"""H3 × time analytics transform.

This module reads `MaugSummarySample` records from Postgres, aggregates them
into H3 spatial bins over fixed time windows, and writes the result to
partitioned Parquet files for fast reuse by the API and offline analysis.

The goal is to keep heavy analytics work in an ETL/ETI transform stage rather
than in request/response handlers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import h3
import pandas as pd
from sqlalchemy import select

from app.backend.eti.db import SessionLocal
from app.backend.eti.models import MaugSummarySample


@dataclass
class H3AnalyticsConfig:
    """Configuration for H3 analytics aggregation.

    Attributes:
        resolution: H3 resolution (integer, e.g. 7 or 8).
        time_freq: Pandas frequency string for binning timestamps (e.g. "1D").
    """

    resolution: int = 7
    time_freq: str = "1D"


def _samples_to_dataframe(samples: Iterable[MaugSummarySample]) -> pd.DataFrame:
    """Convert ORM sample rows into a pandas DataFrame.

    The dataframe schema is intentionally small; additional columns can be
    added later as needed for analytics (e.g. species, detector model).
    """

    rows = []
    for s in samples:
        if s.lat is None or s.lon is None:
            continue
        if s.timestamp_utc is None:
            continue
        rows.append(
            {
                "timestamp_utc": s.timestamp_utc,
                "lat": float(s.lat),
                "lon": float(s.lon),
                "raw_count": int(s.files_count or 0),
                "site_id": s.site_id,
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=["timestamp_utc", "lat", "lon", "raw_count", "site_id"],
        )

    return pd.DataFrame(rows)


def _attach_h3_and_time_bins(df: pd.DataFrame, config: H3AnalyticsConfig) -> pd.DataFrame:
    """Attach H3 index and time bin columns to the dataframe.

    - `h3_index`: resolution `config.resolution` index computed from lat/lon.
    - `time_bin_start`: left edge of the time bin defined by `time_freq`.
    """

    if df.empty:
        return df

    df = df.copy()

    df["h3_index"] = [
        # h3-py v4 API: latlng_to_cell(lat, lng, res)
        h3.latlng_to_cell(row["lat"], row["lon"], config.resolution)
        for _, row in df.iterrows()
    ]

    # Round timestamps down to the nearest frequency bucket.
    df["time_bin_start"] = (
        pd.to_datetime(df["timestamp_utc"])  # ensure tz-aware/naive handled by pandas
        .dt.floor(config.time_freq)
    )

    return df


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-sample rows into H3 × time bins.

    For now we only compute a simple sum of `raw_count` per cell/time bucket
    along with a count of contributing samples. This can be extended later to
    include effort-normalised metrics.
    """

    if df.empty:
        return df

    grouped = (
        df.groupby(["time_bin_start", "h3_index", "site_id"], dropna=False)
        .agg(
            raw_count_sum=("raw_count", "sum"),
            sample_count=("raw_count", "count"),
        )
        .reset_index()
    )

    return grouped


def _write_partitioned_parquet(df: pd.DataFrame, output_dir: Path) -> None:
    """Write aggregated dataframe to partitioned Parquet.

    Data is partitioned by `time_bin_start` (date) to keep files reasonably
    sized and make range filtering cheap for typical map requests.
    """

    output_dir.mkdir(parents=True, exist_ok=True)

    if df.empty:
        return

    df = df.copy()
    df["date"] = pd.to_datetime(df["time_bin_start"]).dt.date

    # Partition by date; each partition is a single Parquet file for now.
    for date_value, part in df.groupby("date"):
        date_str = date_value.strftime("%Y-%m-%d")
        path = output_dir / f"h3_analytics_{date_str}.parquet"
        part.drop(columns=["date"], inplace=True)
        part.to_parquet(path, index=False)


def run_h3_analytics(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    output_dir: Path,
    config: Optional[H3AnalyticsConfig] = None,
) -> Path:
    """Run H3 × time analytics for a given time window.

    Args:
        start: Inclusive start timestamp (UTC). If omitted, processes from the
            earliest available sample.
        end: Exclusive end timestamp (UTC). If omitted, processes up to the
            latest available sample.
        output_dir: Directory where partitioned Parquet files will be written.
        config: Optional `H3AnalyticsConfig`; defaults are reasonable for a
            regional map.

    Returns:
        The `output_dir` path, for convenience in CLIs/tests.
    """

    if config is None:
        config = H3AnalyticsConfig()

    with SessionLocal() as session:
        stmt = select(MaugSummarySample)
        if start is not None:
            stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
        if end is not None:
            stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

        samples = session.scalars(stmt).all()

    df = _samples_to_dataframe(samples)
    df = _attach_h3_and_time_bins(df, config)
    agg = _aggregate(df)
    _write_partitioned_parquet(agg, output_dir)

    return output_dir
