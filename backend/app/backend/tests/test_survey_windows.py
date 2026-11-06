"""Tests for /api/heatmap/survey_windows (local h3_daily Parquet)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

HEX_WEST = "8a182a691a2ffff"
HEX_EAST = "8a182a6828cffff"


def _day(day: date, hexes: list[str], counts: list[int]) -> pd.DataFrame:
    """An h3_analytics-shaped frame for one day."""
    return pd.DataFrame(
        {
            "time_bin_start": pd.to_datetime([day] * len(hexes)),
            "h3_index": hexes,
            "raw_count_sum": counts,
            "sample_count": [100] * len(hexes),
            "detector_nights": [2] * len(hexes),
            "unique_sites": [1] * len(hexes),
            "detections_per_night": [c / 2 for c in counts],
        }
    )


def _write_local(base: Path) -> None:
    """West hex on 16-17 May, east hex on 2 Sep, plus a file whose name must be ignored."""
    base.mkdir()
    _day(date(2024, 5, 16), [HEX_WEST], [40]).to_parquet(
        base / "h3_analytics_2024-05-16.parquet", index=False
    )
    _day(date(2024, 5, 17), [HEX_WEST], [60]).to_parquet(
        base / "h3_analytics_2024-05-17.parquet", index=False
    )
    _day(date(2024, 9, 2), [HEX_EAST], [10]).to_parquet(
        base / "h3_analytics_2024-09-02.parquet", index=False
    )
    _day(date(2024, 9, 3), [HEX_WEST], [999]).to_parquet(
        base / "h3_analytics_notadate.parquet", index=False
    )


def test_survey_windows_local(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_H3_SOURCE", "local")
    base = tmp_path / "h3_daily"
    _write_local(base)

    resp = TestClient(app).get(
        "/api/heatmap/survey_windows", params={"analytics_dir": str(base)}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert [
        (r["h3_index"], r["first_date"], r["last_date"], r["n_days"]) for r in data
    ] == [
        (HEX_WEST, "2024-05-16", "2024-05-17", 2),
        (HEX_EAST, "2024-09-02", "2024-09-02", 1),
    ]
    west = data[0]
    assert west["raw_count_sum"] == 100.0
    assert west["detector_nights"] == 4
    assert west["detections_per_night"] == 25.0
    assert 51 < west["lat"] < 52 and -10 < west["lon"] < -9


def test_survey_windows_missing_dir(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_H3_SOURCE", "local")

    resp = TestClient(app).get(
        "/api/heatmap/survey_windows",
        params={"analytics_dir": str(tmp_path / "missing")},
    )

    assert resp.status_code == 200
    assert resp.json() == []
