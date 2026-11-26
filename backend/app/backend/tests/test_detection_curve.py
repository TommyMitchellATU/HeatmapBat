"""Tests for /api/occupancy/detection_curve (local and S3)."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.main import app


def _curve() -> pd.DataFrame:
    """A notebook-shaped curve: three nights, the middle one not surveyed, written out of order."""
    return pd.DataFrame(
        {
            "night": [date(2024, 7, 22), date(2024, 7, 20), date(2024, 7, 21)],
            "doy": [204, 202, 203],
            "p_mean": [0.90, 0.95, 0.93],
            "p_median": [0.91, 0.96, 0.94],
            "p_lo": [0.85, 0.92, 0.89],
            "p_hi": [0.94, 0.98, 0.97],
            "interval": [0.9, 0.9, 0.9],
            "n_detected": [9, 12, 0],
            "n_surveys": [10, 13, 0],
            "observed_rate": [0.9, 12 / 13, np.nan],
            "rate_lo": [0.7, 0.77, np.nan],
            "rate_hi": [1.0, 1.0, np.nan],
        }
    )


def test_curve_local_sorted_with_null_rate(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_PREDICTIONS_SOURCE", "local")
    path = tmp_path / "detection_curve.parquet"
    _curve().to_parquet(path, index=False)

    resp = TestClient(app).get(
        "/api/occupancy/detection_curve", params={"curve_path": str(path)}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert [r["night"] for r in data] == ["2024-07-20", "2024-07-21", "2024-07-22"]
    assert data[1]["observed_rate"] is None
    assert data[1]["rate_lo"] is None and data[1]["rate_hi"] is None
    assert data[1]["n_surveys"] == 0
    assert data[2]["observed_rate"] == 0.9
    assert data[2]["rate_lo"] == 0.7 and data[2]["rate_hi"] == 1.0
    assert data[0]["p_lo"] < data[0]["p_median"] < data[0]["p_hi"]


def test_curve_missing_file_returns_empty(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_PREDICTIONS_SOURCE", "local")

    resp = TestClient(app).get(
        "/api/occupancy/detection_curve",
        params={"curve_path": str(tmp_path / "nope.parquet")},
    )

    assert resp.status_code == 200
    assert resp.json() == []


def test_curve_read_from_s3(monkeypatch: Any) -> None:
    key = "analytics/occupancy_curves_test/detection_curve.parquet"
    buf = BytesIO()
    _curve().to_parquet(buf, index=False)

    def _fake_get_object_bytes(requested: str) -> bytes:
        assert requested == key
        return buf.getvalue()

    monkeypatch.delenv("HEATMAP_PREDICTIONS_SOURCE", raising=False)
    monkeypatch.setenv("HEATMAP_SOURCE", "s3")
    monkeypatch.setattr("app.main.get_object_bytes", _fake_get_object_bytes)

    resp = TestClient(app).get(
        "/api/occupancy/detection_curve", params={"curve_path": key}
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_curve_without_rate_range_columns(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_PREDICTIONS_SOURCE", "local")
    path = tmp_path / "detection_curve.parquet"
    _curve().drop(columns=["rate_lo", "rate_hi"]).to_parquet(path, index=False)

    resp = TestClient(app).get(
        "/api/occupancy/detection_curve", params={"curve_path": str(path)}
    )

    assert resp.status_code == 200
    assert resp.json()[0]["rate_lo"] is None
