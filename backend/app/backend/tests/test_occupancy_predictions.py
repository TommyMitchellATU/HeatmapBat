"""Tests for /api/occupancy/predictions and /api/occupancy/nights (local and S3)."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

HEX_A = "8a1825695987fff"
HEX_B = "8a182a68218ffff"


def _rows(night: date, hexes: list[str], observed: list[int]) -> pd.DataFrame:
    """A notebook-shaped predictions frame for one night."""
    return pd.DataFrame(
        {
            "night": [night] * len(hexes),
            "h3_index": hexes,
            "observed": observed,
            "pred_mean": [500.0] * len(hexes),
            "pred_median": [300.0] * len(hexes),
            "pred_lo": [50.0] * len(hexes),
            "pred_hi": [1200.0] * len(hexes),
            "pred_interval": [0.9] * len(hexes),
            "n_detectors": [12] * len(hexes),
            "prediction_kind": ["out_of_sample"] * len(hexes),
        }
    )


def _write_local(base: Path) -> None:
    """Two nights of predictions plus a file whose name must be ignored."""
    base.mkdir()
    _rows(date(2024, 7, 20), [HEX_A, HEX_B], [99, 1292]).to_parquet(
        base / "occupancy_predictions_2024-07-20.parquet", index=False
    )
    _rows(date(2024, 7, 21), [HEX_A], [150]).to_parquet(
        base / "occupancy_predictions_2024-07-21.parquet", index=False
    )
    _rows(date(2024, 7, 22), [HEX_A], [1]).to_parquet(
        base / "occupancy_predictions_notadate.parquet", index=False
    )


def test_predictions_local_filters_by_night(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_PREDICTIONS_SOURCE", "local")
    base = tmp_path / "preds"
    _write_local(base)

    resp = TestClient(app).get(
        "/api/occupancy/predictions",
        params={
            "start": "2024-07-20",
            "end": "2024-07-21",
            "predictions_dir": str(base),
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert [(r["night"], r["h3_index"], r["observed"]) for r in data] == [
        ("2024-07-20", HEX_A, 99),
        ("2024-07-20", HEX_B, 1292),
    ]
    row = data[0]
    assert row["pred_median"] == 300.0
    assert row["pred_interval"] == 0.9
    assert row["prediction_kind"] == "out_of_sample"
    assert len(row["polygon"]) == 6
    assert 54 < row["lat"] < 55 and -8 < row["lon"] < -7


def test_nights_local_sums_hexes_per_night(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_PREDICTIONS_SOURCE", "local")
    base = tmp_path / "preds"
    _write_local(base)

    resp = TestClient(app).get(
        "/api/occupancy/nights", params={"predictions_dir": str(base)}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["nights"] == [
        {"night": "2024-07-20", "observed": 1391, "pred_median": 600.0, "n_hexes": 2},
        {"night": "2024-07-21", "observed": 150, "pred_median": 300.0, "n_hexes": 1},
    ]
    assert body["min_night"] == "2024-07-20"
    assert body["max_night"] == "2024-07-21"


def test_missing_dir_returns_empty(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_PREDICTIONS_SOURCE", "local")
    client = TestClient(app)
    missing = str(tmp_path / "nope")

    preds = client.get(
        "/api/occupancy/predictions", params={"predictions_dir": missing}
    )
    nights = client.get("/api/occupancy/nights", params={"predictions_dir": missing})

    assert preds.json() == []
    assert nights.json() == {"nights": [], "min_night": None, "max_night": None}


def test_predictions_source_overrides_shared(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("HEATMAP_SOURCE", "s3")
    monkeypatch.setenv("HEATMAP_PREDICTIONS_SOURCE", "local")
    base = tmp_path / "preds"
    _write_local(base)

    def _no_s3(prefix: str) -> Iterable[str]:
        raise AssertionError(
            "S3 must not be used when HEATMAP_PREDICTIONS_SOURCE=local"
        )

    monkeypatch.setattr("app.main.list_keys", _no_s3)

    resp = TestClient(app).get(
        "/api/occupancy/predictions", params={"predictions_dir": str(base)}
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_predictions_read_from_s3(monkeypatch: Any) -> None:
    prefix = "analytics/occupancy_predictions_test"
    buf = BytesIO()
    _rows(date(2024, 7, 20), [HEX_B], [1292]).to_parquet(buf, index=False)
    objects = {
        f"{prefix}/occupancy_predictions_2024-07-20.parquet": buf.getvalue(),
        f"{prefix}/unrelated.parquet": b"not parquet",
    }

    def _fake_list_keys(prefix_arg: str) -> Iterable[str]:
        return [key for key in objects if key.startswith(prefix_arg)]

    def _fake_get_object_bytes(key: str) -> bytes:
        return objects[key]

    monkeypatch.delenv("HEATMAP_PREDICTIONS_SOURCE", raising=False)
    monkeypatch.setenv("HEATMAP_SOURCE", "s3")
    monkeypatch.setattr("app.main.list_keys", _fake_list_keys)
    monkeypatch.setattr("app.main.get_object_bytes", _fake_get_object_bytes)

    resp = TestClient(app).get(
        "/api/occupancy/predictions", params={"predictions_dir": prefix}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["h3_index"] == HEX_B
    assert data[0]["observed"] == 1292
