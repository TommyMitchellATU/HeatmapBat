from __future__ import annotations

import argparse
from pathlib import Path

from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MAUG summary file into DB.")
    parser.add_argument("path", type=str, help="Path to MAUG *_Summary.txt file")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    db = SessionLocal()
    try:
        count = load_summary_file(db, path)
    finally:
        db.close()

    print(f"Imported {count} rows from {path}")


if __name__ == "__main__":
    main()