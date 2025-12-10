"""Export helpers for turning ETI samples into flat files.

This module currently focuses on writing map‑ready CSV files from the
``maug_summary_samples`` table. The exported format is intentionally simple so
that spreadsheets, GIS tools, or web maps can consume it without any
additional processing.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.eti.models import MaugSummarySample


def _iter_samples(
    db: Session,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Iterable[MaugSummarySample]:
    """Yield ``MaugSummarySample`` rows filtered by an optional date range.

    This helper keeps the query logic in one place so both CSV and GeoJSON
    exporters (and any future loaders) can share the same selection
    semantics.
    """

    # Start from the full table.
    stmt = select(MaugSummarySample)

    # Apply inclusive lower bound on timestamp, if provided.
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)

    # Apply exclusive upper bound on timestamp, if provided.
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

    # Stream results back as ORM instances rather than raw rows.
    for row in db.execute(stmt).scalars():
        yield row


def export_samples_to_csv(
    db: Session,
    out_path: Path,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> int:
    """Export samples to a CSV file and return the number of rows written.

    Parameters
    ----------
    db:
        An open SQLAlchemy :class:`Session` bound to the application
        database.
    out_path:
        Filesystem path where the CSV should be written. Parent
        directories will be created automatically.
    start, end:
        Optional date bounds on ``timestamp_utc``. ``start`` is inclusive
        and ``end`` is exclusive, which makes it easy to export whole days
        (e.g. ``--start 2024-05-16 --end 2024-05-17``).

    Returns
    -------
    int
        The number of data rows written (not counting the header).
    """

    import csv

    # Ensure the output directory exists so callers can safely point at
    # nested paths such as ``/data/exports/...``.
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "timestamp_utc",
        "lat",
        "lon",
        "power_v",
        "temp_c",
        "files_count",
        "scrubbed_count",
        "mic0_type",
        "raw_date",
        "raw_time",
    ]

    count = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        # Iterate over filtered rows and write one CSV line per sample.
        for row in _iter_samples(db, start=start, end=end):
            writer.writerow(
                {
                    "id": row.id,
                    "timestamp_utc": row.timestamp_utc.isoformat(),
                    "lat": row.lat,
                    "lon": row.lon,
                    "power_v": row.power_v,
                    "temp_c": row.temp_c,
                    "files_count": row.files_count,
                    "scrubbed_count": row.scrubbed_count,
                    "mic0_type": row.mic0_type,
                    "raw_date": row.raw_date,
                    "raw_time": row.raw_time,
                }
            )
            count += 1

    return count
