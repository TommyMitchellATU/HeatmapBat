from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import (
    derive_source_folder,
    find_summary_files,
    load_summary_file,
)

"""Command-line entrypoint for importing detector summary files.

Imports one ``*_Summary.txt`` file, or every one under a directory (sub-folders
included, each tagged with its folder as ``source_folder``).
"""


def main() -> None:
    """Parse arguments, import the file or directory, and report row counts."""

    parser = argparse.ArgumentParser(
        description="Import detector *_Summary.txt files into the database.",
    )
    parser.add_argument(
        "path",
        type=str,
        help="A *_Summary.txt file, or a directory searched recursively",
    )
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help="Folder that source_folder flags are relative to (default: the "
        "directory given, or the file's own folder)",
    )

    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        # Using SystemExit keeps the process exit code non‑zero while still
        # producing a friendly message on stderr.
        raise SystemExit(f"File not found: {path}")

    files = find_summary_files(path) if path.is_dir() else [path]
    root = Path(args.root) if args.root else (path if path.is_dir() else path.parent)

    total = 0
    db = SessionLocal()
    try:
        for file in files:
            folder: Optional[str] = derive_source_folder(file, root)
            count = load_summary_file(db, file, source_folder=folder)
            total += count
            print(f"Imported {count} rows from {file} (folder: {folder or '-'})")
    finally:
        db.close()

    print(f"Done: {len(files)} files, {total} rows")


if __name__ == "__main__":
    main()
