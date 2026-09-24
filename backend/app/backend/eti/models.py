from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

"""SQLAlchemy ORM models for the ETI subsystem.

Only model a single table, ``maug_summary_samples``, which holds
one row per line from a detector ``*_Summary.txt`` file. This is the canonical
representation that ETL code writes to and that the API and analysis tools
will read from.
"""


class Base(DeclarativeBase):
    """Base class for all ORM models in this package."""


class MaugSummarySample(Base):
    """Sample row imported from a bat detector ``*_Summary.txt`` file.

    The table stores both parsed, typed fields (e.g. ``lat``, ``lon``,
    ``timestamp_utc``) and some of the original string values (``raw_date``,
    ``raw_time``) so that downstream tools can re‑inspect or re‑parse the
    source data if needed.
    """

    __tablename__ = "maug_summary_samples"

    # Surrogate primary key used internally by the database.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Filename prefix before the first dash ("D01", "MEEN"); legacy, not a real site.
    site_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Detector serial from the filename ("6771"); the physical device.
    detector_serial: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Sub-folder the file was imported from ("special/D06", "NA"); None at the top level.
    source_folder: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamp reconstructed from the DATE/TIME columns in the summary file.
    timestamp_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
    )

    # Geographic location of the sample.
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    # Telemetry extracted from the file (may be missing for some rows).
    power_v: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    files_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scrubbed_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    mic0_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Raw string values preserved from the input for traceability.
    raw_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)
