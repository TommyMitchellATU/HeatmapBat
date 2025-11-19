from __future__ import annotations

import argparse
from pathlib import Path

from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file

"""Command-line entrypoint for importing MAUG summary files.

This small CLI is designed to be run either from within the Docker ``api``
container or any environment that has access to the target Postgres database
and the MAUG ``*_Summary.txt`` files. It wires command‑line parsing to the
ETI parsing helpers and database session utilities.
"""


def main() -> None:
    """Parse arguments, import the given summary file, and report row count."""

    parser = argparse.ArgumentParser(
        description="Import a MAUG *_Summary.txt file into the database.",
    )
    parser.add_argument(
        "path",
        type=str,
        help="Path to MAUG *_Summary.txt file to import",
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        # Using SystemExit keeps the process exit code non‑zero while still
        # producing a friendly message on stderr.
        raise SystemExit(f"File not found: {path}")

    db = SessionLocal()
    try:
        count = load_summary_file(db, path)
    finally:
        db.close()

    print(f"Imported {count} rows from {path}")


if __name__ == "__main__":
    main()
