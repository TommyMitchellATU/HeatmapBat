from __future__ import annotations

"""GeoJSON export helpers for ETI samples.

These utilities turn rows from ``maug_summary_samples`` into a GeoJSON
FeatureCollection that can be consumed by web maps or GIS tools without any
extra transformation.
"""

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.eti.models import MaugSummarySample


def _iter_samples(
    db: Session,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Iterable[MaugSummarySample]:
    """Internal helper to stream filtered ``MaugSummarySample`` rows.

    Keeping this separate mirrors the CSV exporter and allows both to share
    consistent date‑range semantics.
    """

    stmt = select(MaugSummarySample)
    if start is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc >= start)
    if end is not None:
        stmt = stmt.where(MaugSummarySample.timestamp_utc < end)

    for row in db.execute(stmt).scalars():
        yield row


def export_samples_to_geojson(
    db: Session,
    out_path: Path,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> int:
    """Export samples to a GeoJSON FeatureCollection.

    Parameters
    ----------
    db:
        Active SQLAlchemy :class:`Session` connected to the app database.
    out_path:
        Destination file for the GeoJSON document.
    start, end:
        Optional date bounds on ``timestamp_utc`` (inclusive ``start``,
        exclusive ``end``) matching the CSV exporter behaviour.

    Returns
    -------
    int
        Number of features written to the collection.
    """

    features: list[dict[str, Any]] = []
    count = 0

    # Build one GeoJSON Feature per sample.
    for row in _iter_samples(db, start=start, end=end):
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                # GeoJSON expects [lon, lat] order.
                "coordinates": [row.lon, row.lat],
            },
            "properties": {
                "id": row.id,
                "timestamp_utc": row.timestamp_utc.isoformat(),
                "power_v": row.power_v,
                "temp_c": row.temp_c,
                "files_count": row.files_count,
                "scrubbed_count": row.scrubbed_count,
                "mic0_type": row.mic0_type,
                "raw_date": row.raw_date,
                "raw_time": row.raw_time,
            },
        }
        features.append(feature)
        count += 1

    collection = {"type": "FeatureCollection", "features": features}

    # Ensure the output folder exists, then write the JSON in a single shot.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(collection), encoding="utf-8")

    return count
