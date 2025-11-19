from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""


class MaugSummarySample(Base):
    __tablename__ = "maug_summary_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    power_v: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    files_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scrubbed_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    mic0_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    raw_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_time: Mapped[Optional[str]] = mapped_column(String, nullable=True)