from __future__ import annotations

from pathlib import Path

from app.backend.eti.db import SessionLocal
from app.backend.eti.load.export import export_samples_to_csv
from app.backend.eti.models import MaugSummarySample


def test_export_samples_to_csv_tmp_path(tmp_path: Path) -> None:
    """Basic smoke test: insert a row and ensure it is exported."""

    db = SessionLocal()
    try:
        sample = MaugSummarySample(
            timestamp_utc="2024-05-16T20:00:00",  # type: ignore[arg-type]
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
