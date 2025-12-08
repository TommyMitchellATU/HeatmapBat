from __future__ import annotations

"""Command-line tool for exporting ETI samples to CSV.

This CLI is intended to be run either in the ``api`` container or any
environment that can reach the Postgres instance described by
``DATABASE_URL``. It uses the same database session utilities as the rest of
ETI.
"""

import argparse
from datetime import datetime
from pathlib import Path

from app.backend.eti.db import SessionLocal
from app.backend.eti.load.export import export_samples_to_csv


def _parse_date(value: str) -> datetime:
    """Parse a ``YYYY-MM-DD`` date string into a ``datetime`` at midnight.

    This is sufficient for coarse daily filtering over ``timestamp_utc``.
    """

    return datetime.strptime(value, "%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export rows from maug_summary_samples to a CSV file for mapping "
            "or analysis."
        ),
    )

    parser.add_argument(
        "out_path",
        type=Path,
        help="Filesystem path for the output CSV file",
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
        count = export_samples_to_csv(
            db,
            out_path=args.out_path,
            start=args.start,
            end=args.end,
        )
    finally:
        db.close()

    print(f"Exported {count} rows to {args.out_path}")


if __name__ == "__main__":  # pragma: no cover - manual CLI entrypoint
    main()
