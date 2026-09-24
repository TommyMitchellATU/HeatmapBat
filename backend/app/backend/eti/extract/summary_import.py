from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from app.backend.eti.models import MaugSummarySample


"""Helpers for parsing bat detector ``*_Summary.txt`` files into ORM objects.

The detector summary files are CSV-like text files with columns such as DATE, TIME,
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

    The detector export uses an abbreviated month name, for example::

        DATE = "2024-May-16"
        TIME = "20:55:59"

    which we parse using the ``"%Y-%b-%d %H:%M:%S"`` strptime pattern.
    """

    return datetime.strptime(
        f"{date_str.strip()} {time_str.strip()}", "%Y-%b-%d %H:%M:%S"
    )


def _derive_site_id_from_filename(source_path: Path) -> Optional[str]:
    """Return the filename prefix before the first dash (the legacy ``site_id``).

    ``D01-MEEN-6771_A_Summary.txt`` -> ``"D01"``, ``MEEN-6771_A_Summary.txt`` -> ``"MEEN"``.
    Still feeds ``h3_daily``; use :func:`parse_detector_serial` for detector identity.
    """

    stem = source_path.stem  # e.g. "D04-BAT-3992_A_Summary"

    # TODO: the prefix is an old processing-folder name, not a site - fix with h3_daily.
    first = stem.split("-", 1)[0].strip()
    return first or None


# Matches the serial in names like "D01-MEEN-6771_A_Summary" or "GANN-4035_A_Summary".
_SERIAL_PATTERN = re.compile(r"-(\d+)_[A-Za-z]+_Summary$")


def parse_detector_serial(source_path: Path) -> Optional[str]:
    """Return the detector serial from a summary filename, or None if absent.

    ``D01-MEEN-6771_A_Summary.txt`` -> ``"6771"``. The serial names the physical
    device, which moves between wind farms, so it is the only stable detector id.
    """

    match = _SERIAL_PATTERN.search(source_path.stem)
    return match.group(1) if match else None


def derive_source_folder(source_path: Path, root: Path) -> Optional[str]:
    """Return the file's folder relative to the import root, or None at the root.

    ``root/special/D06/x_Summary.txt`` -> ``"special/D06"``. Used as the flag for
    files set aside in sub-folders (``NA/``, ``special/``).
    """

    relative = source_path.parent.resolve().relative_to(root.resolve())
    return relative.as_posix() if relative.parts else None


def find_summary_files(root: Path) -> List[Path]:
    """Return every ``*_Summary.txt`` under ``root``, including sub-folders, sorted."""

    return sorted(root.rglob("*_Summary.txt"))


def _parse_rows(
    rows: Iterable[dict[str, str]],
    source_path: Path,
    source_folder: Optional[str] = None,
) -> List[MaugSummarySample]:
    items: List[MaugSummarySample] = []

    site_id = _derive_site_id_from_filename(source_path)
    detector_serial = parse_detector_serial(source_path)

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
                site_id=site_id,
                detector_serial=detector_serial,
                source_folder=source_folder,
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


def parse_summary_file(
    path: Path, source_folder: Optional[str] = None
) -> List[MaugSummarySample]:
    """Parse a detector summary file; ``source_folder`` is stored as its flag."""

    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        return _parse_rows(reader, source_path=path, source_folder=source_folder)


def load_summary_file(
    db: Session, path: Path, source_folder: Optional[str] = None
) -> int:
    """Parse a summary file and insert all rows in a single transaction.

    Parameters
    ----------
    db:
        An active SQLAlchemy :class:`Session` bound to the target database.
    path:
        Filesystem path to the detector ``*_Summary.txt`` file to be imported.
    source_folder:
        Folder flag from :func:`derive_source_folder`; None for top-level files.

    Returns
    -------
    int
        The number of :class:`MaugSummarySample` rows persisted.
    """

    samples = parse_summary_file(path, source_folder=source_folder)
    db.add_all(samples)
    db.commit()
    return len(samples)
