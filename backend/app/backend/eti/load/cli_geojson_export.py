from __future__ import annotations

"""CLI for exporting ETI samples as a GeoJSON FeatureCollection."""

import argparse
from datetime import datetime
from pathlib import Path

from app.backend.eti.db import SessionLocal
from app.backend.eti.load.geojson_export import export_samples_to_geojson


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export maug_summary_samples rows to a GeoJSON file.",
    )

    parser.add_argument(
        "out_path",
        type=Path,
        help="Filesystem path for the output GeoJSON file",
    )

    parser.add_argument(
        "--start",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        help="Inclusive start date on timestamp_utc (UTC)",
    )

    parser.add_argument(
        "--end",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        help="Exclusive end date on timestamp_utc (UTC)",
    )

    args = parser.parse_args()

    db = SessionLocal()
    try:
        count = export_samples_to_geojson(
            db,
            out_path=args.out_path,
            start=args.start,
            end=args.end,
        )
    finally:
        db.close()

    print(f"Exported {count} features to {args.out_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
