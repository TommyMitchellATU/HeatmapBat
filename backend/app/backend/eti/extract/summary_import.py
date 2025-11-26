from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from sqlalchemy.orm import Session

from app.backend.eti.models import MaugSummarySample


""" Todo allow multiple diffrerent file formats

Helpers for parsing MAUG ``*_Summary.txt`` files into ORM objects.

The MAUG summary files are CSV‑like text files with columns such as DATE, TIME,
LAT, LON, NS, EW, POWER(V), TEMP(C), and others. This module is responsible
for:

* Converting raw string fields (e.g. ``"2024-May-16"``, ``"20:55:59"``) into
    typed Python values (``datetime``, ``float``, ``int``).
* Constructing :class:`app.backend.eti.models.MaugSummarySample` instances.
* Persisting those instances to the database in a single transaction.
"""


def parse_lat_lon(
    lat_str: str,
    ns_str: str,
    lon_str: str,
    ew_str: str,
) -> tuple[float, float]:
    lat = float(lat_str.strip())
    if ns_str.strip().lower() == "s":
        lat = -lat

    lon = float(lon_str.strip())
    if ew_str.strip().lower() == "w":
        lon = -lon

    return lat, lon


def parse_timestamp(date_str: str, time_str: str) -> datetime:
    """Combine separate DATE/TIME fields into a single ``datetime``.

    The MAUG export uses an abbreviated month name, for example::

        DATE = "2024-May-16"
        TIME = "20:55:59"

    which we parse using the ``"%Y-%b-%d %H:%M:%S"`` strptime pattern.
    """

    return datetime.strptime(
        f"{date_str.strip()} {time_str.strip()}", "%Y-%b-%d %H:%M:%S"
    )


def _parse_rows(rows: Iterable[dict[str, str]]) -> List[MaugSummarySample]:
    items: List[MaugSummarySample] = []

    for row in rows:
        if not row.get("DATE") or not row.get("TIME"):
            continue

        date_str = row["DATE"]
        time_str = row["TIME"]
        lat_str = row["LAT"]
        ns_str = row["NS"]
        lon_str = row["LON"]
        ew_str = row["EW"]

        power_str = (row.get("POWER(V)") or "").strip()
        temp_str = (row.get("TEMP(C)") or "").strip()
        files_str = (row.get("#FILES") or "").strip()
        scrubbed_str = (row.get("#SCRUBBED") or "").strip()
        mic0_type = (row.get("MIC0 TYPE") or "").strip()

        timestamp = parse_timestamp(date_str, time_str)
        lat, lon = parse_lat_lon(lat_str, ns_str, lon_str, ew_str)

        power_v = float(power_str) if power_str else None
        temp_c = float(temp_str) if temp_str else None
        files_count = int(files_str) if files_str else None
        scrubbed_count = int(scrubbed_str) if scrubbed_str else None

        items.append(
            MaugSummarySample(
                timestamp_utc=timestamp,
                lat=lat,
                lon=lon,
                power_v=power_v,
                temp_c=temp_c,
                files_count=files_count,
                scrubbed_count=scrubbed_count,
                mic0_type=mic0_type or None,
                raw_date=date_str.strip(),
                raw_time=time_str.strip(),
            )
        )

    return items


def parse_summary_file(path: Path) -> List[MaugSummarySample]:
    """Parse a MAUG summary file from the local filesystem."""

    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        return _parse_rows(reader)


def load_summary_file(db: Session, path: Path) -> int:
    """Parse a summary file and insert all rows in a single transaction.

    Parameters
    ----------
    db:
        An active SQLAlchemy :class:`Session` bound to the target database.
    path:
        Filesystem path to the MAUG ``*_Summary.txt`` file to be imported.

    Returns
    -------
    int
        The number of :class:`MaugSummarySample` rows persisted.
    """

    samples = parse_summary_file(path)
    db.add_all(samples)
    db.commit()
    return len(samples)
