from __future__ import annotations

"""Integration-style tests for ETI export helpers.

These tests talk to the same Postgres instance used by the application,
which means they should be run inside the Docker stack (for example via

    docker compose exec -w /app api uv run pytest app/backend/tests/test_export.py -q

They are intentionally lightweight smoke tests rather than exhaustive
verification.
"""

from datetime import datetime
from pathlib import Path

from app.backend.eti.db import SessionLocal
from app.backend.eti.load.export import export_samples_to_csv
from app.backend.eti.models import MaugSummarySample


def test_export_samples_to_csv_tmp_path(tmp_path: Path) -> None:
    """Insert a row and ensure it is written to a CSV file.

    This exercises the happy path of ``export_samples_to_csv`` against a
    real database connection, verifying that:

    * inserting a ``MaugSummarySample`` succeeds,
    * the exporter creates the target file, and
    * the file contains the expected header columns.
    """

    db = SessionLocal()
    try:
        sample = MaugSummarySample(
            timestamp_utc=datetime(2024, 5, 16, 20, 0, 0),
            lat=54.0,
            lon=-7.0,
            power_v=5.0,
            temp_c=10.0,
            files_count=1,
            scrubbed_count=0,
            mic0_type="test",
            raw_date="2024-May-16",
            raw_time="20:00:00",
        )
        db.add(sample)
        db.commit()

        out_path = tmp_path / "export.csv"
        count = export_samples_to_csv(db, out_path)
    finally:
        db.close()

    assert count >= 1
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "lat" in text and "lon" in text
