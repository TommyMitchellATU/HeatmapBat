from __future__ import annotations

"""Export helpers for turning ETI samples into flat files.

Right now this module focuses on writing map‑ready CSV files from the
``maug_summary_samples`` table. The goal is to keep the public surface small
and easy to call from both CLIs and tests.
"""

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

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    start, end:
        Inclusive start and exclusive end bounds on ``timestamp_utc``. If
        either is ``None`` it is not applied.
    """

    stmt = select(MaugSummarySample)
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

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

    The CSV columns are chosen to be immediately useful for mapping and
    analysis tools: identifiers, timestamp, location, telemetry, and raw
    string fields from the source files.
    """

    import csv

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
