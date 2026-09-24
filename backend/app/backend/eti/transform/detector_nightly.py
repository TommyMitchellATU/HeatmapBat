"""Per-detector nightly survey table for occupancy modelling.

Reads ``MaugSummarySample`` rows and writes one row per night x H3 site x detector
serial to ``detector_nightly_YYYY-MM-DD.parquet``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import h3
import pandas as pd
from sqlalchemy import select

from app.backend.eti.db import SessionLocal
from app.backend.eti.models import MaugSummarySample

logger = logging.getLogger(__name__)

# Hour at which one survey night ends and the next begins (noon-to-noon).
NIGHT_START_HOUR = 12

# H3 resolution that defines an occupancy site; 10 gives one hex per deployment.
SITE_H3_RESOLUTION = 10

OUTPUT_COLUMNS = [
    "night",
    "h3_index",
    "detector_serial",
    "source_folder",
    "raw_count_sum",
    "sample_count",
]


@dataclass
class DetectorNightlyConfig:
    """Settings for the detector-nightly transform.

    Attributes:
        resolution: H3 resolution used to define a site.
        night_start_hour: Hour, in the timestamps' own clock, at which a night rolls over.
    """

    resolution: int = SITE_H3_RESOLUTION
    night_start_hour: int = NIGHT_START_HOUR


def _samples_to_dataframe(samples: Iterable[Any]) -> pd.DataFrame:
    """Convert sample rows to a DataFrame, dropping rows with no detector serial."""

    rows = [
        {
            "timestamp_utc": s.timestamp_utc,
            "lat": float(s.lat),
            "lon": float(s.lon),
            "raw_count": int(s.files_count or 0),
            "detector_serial": s.detector_serial,
            "source_folder": s.source_folder,
        }
        for s in samples
        if s.lat is not None and s.lon is not None and s.timestamp_utc is not None
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp_utc",
            "lat",
            "lon",
            "raw_count",
            "detector_serial",
            "source_folder",
        ],
    )

    missing = df["detector_serial"].isna()
    if missing.any():
        logger.warning("Dropping %d samples with no detector serial", missing.sum())
    return df[~missing].reset_index(drop=True)


def assign_night(timestamps: pd.Series, night_start_hour: int) -> pd.Series:
    """Label each timestamp with the date its survey night started.

    With a noon start, 2024-05-15 20:00 and 2024-05-16 03:00 are both night 2024-05-15.
    """

    shifted = pd.to_datetime(timestamps) - pd.Timedelta(hours=night_start_hour)
    return shifted.dt.normalize()


def _attach_site_and_night(
    df: pd.DataFrame, config: DetectorNightlyConfig
) -> pd.DataFrame:
    """Add the ``h3_index`` site and ``night`` label columns."""

    df = df.copy()
    df["h3_index"] = [
        h3.latlng_to_cell(lat, lon, config.resolution)
        for lat, lon in zip(df["lat"], df["lon"])
    ]
    df["night"] = assign_night(df["timestamp_utc"], config.night_start_hour)
    return df


def _join_folders(folders: pd.Series) -> Optional[str]:
    """Join the distinct non-null folder flags in a group, or None if all are top-level."""

    distinct = sorted(folders.dropna().unique())
    return ",".join(distinct) if distinct else None


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse samples to one survey row per night x site x detector."""

    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # RULE: Do not add source_folder (or the source file) to this key. A detector that
    # swaps cards mid-night (3591 A -> B at 03:45) must stay ONE survey, not two;
    # the folder flags are joined instead. [doc: Detector-nightly analytics]
    grouped = (
        df.groupby(["night", "h3_index", "detector_serial"])
        .agg(
            source_folder=("source_folder", _join_folders),
            raw_count_sum=("raw_count", "sum"),
            sample_count=("raw_count", "count"),
        )
        .reset_index()
    )
    return grouped[OUTPUT_COLUMNS]


def _write_partitioned_parquet(df: pd.DataFrame, output_dir: Path) -> None:
    """Write one ``detector_nightly_YYYY-MM-DD.parquet`` file per night."""

    output_dir.mkdir(parents=True, exist_ok=True)
    for night, part in df.groupby("night"):
        path = output_dir / f"detector_nightly_{night:%Y-%m-%d}.parquet"
        part.to_parquet(path, index=False)


def build_detector_nightly(
    samples: Iterable[Any], config: Optional[DetectorNightlyConfig] = None
) -> pd.DataFrame:
    """Turn sample rows into the detector-nightly survey table (no I/O)."""

    config = config or DetectorNightlyConfig()
    df = _samples_to_dataframe(samples)
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return _aggregate(_attach_site_and_night(df, config))


def run_detector_nightly(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    output_dir: Path,
    config: Optional[DetectorNightlyConfig] = None,
) -> Path:
    """Read samples from the database and write the detector-nightly Parquet files.

    Args:
        start: Inclusive start timestamp filter; None reads from the earliest sample.
        end: Exclusive end timestamp filter; None reads to the latest sample.
        output_dir: Directory for the partitioned Parquet files.
        config: Optional ``DetectorNightlyConfig``; defaults to res-10, noon-to-noon.

    Returns:
        The ``output_dir`` path.
    """

    with SessionLocal() as session:
        stmt = select(MaugSummarySample)
        if start is not None:
            stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
        if end is not None:
            stmt = stmt.where(MaugSummarySample.timestamp_utc < end)
        samples = session.scalars(stmt).all()

    table = build_detector_nightly(samples, config)
    _write_partitioned_parquet(table, output_dir)
    return output_dir
