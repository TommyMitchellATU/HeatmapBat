"""Tests for /api/activity/* (detector_nightly Parquet, local and S3)."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from fastapi.testclient import TestClient

from app import main
from app.main import app

HEX_WEST = "8a182a691a2ffff"
HEX_EAST = "8a182a6828cffff"


def _night(night: date, rows: list[tuple[str, str, int]]) -> pd.DataFrame:
    """A detector_nightly-shaped frame: one (hex, serial, detections) tuple per row."""
    return pd.DataFrame(
        {
            "night": pd.to_datetime([night] * len(rows)),
            "h3_index": [r[0] for r in rows],
            "detector_serial": [r[1] for r in rows],
            "source_folder": [None] * len(rows),
            "raw_count_sum": [r[2] for r in rows],
            "sample_count": [600] * len(rows),
        }
    )


def _write_local(base: Path) -> None:
    """West hex: 2 detectors on 16 May, 1 on 17 May. East hex: 1 on 2 Sep. Plus a bad name."""
    base.mkdir()
    _night(
        date(2024, 5, 16), [(HEX_WEST, "1001", 30), (HEX_WEST, "1002", 10)]
    ).to_parquet(base / "detector_nightly_2024-05-16.parquet", index=False)
    _night(date(2024, 5, 17), [(HEX_WEST, "1001", 60)]).to_parquet(
        base / "detector_nightly_2024-05-17.parquet", index=False
    )
    _night(date(2024, 9, 2), [(HEX_EAST, "2001", 10)]).to_parquet(
        base / "detector_nightly_2024-09-02.parquet", index=False
    )
    _night(date(2024, 9, 3), [(HEX_WEST, "1001", 999)]).to_parquet(
        base / "detector_nightly_notadate.parquet", index=False
    )


def _client(monkeypatch: Any) -> TestClient:
    """Test client with the activity routes reading local files."""
    monkeypatch.setenv("HEATMAP_ACTIVITY_SOURCE", "local")
    return TestClient(app)


def test_hexes_sum_detector_nights(monkeypatch: Any, tmp_path: Path) -> None:
    base = tmp_path / "detector_nightly"
    _write_local(base)

    resp = _client(monkeypatch).get(
        "/api/activity/hexes", params={"nightly_dir": str(base)}
    )

    assert resp.status_code == 200
    by_hex = {r["h3_index"]: r for r in resp.json()}
    assert set(by_hex) == {HEX_WEST, HEX_EAST}
    west = by_hex[HEX_WEST]
    assert west["raw_count_sum"] == 100
    assert west["sample_count"] == 1800
    assert west["detector_nights"] == 3
    assert west["n_detectors"] == 2
    assert west["detections_per_night"] == 33.33
    assert 51 < west["lat"] < 52 and -10 < west["lon"] < -9
    assert len(west["polygon"]) == 6


def test_hexes_night_range(monkeypatch: Any, tmp_path: Path) -> None:
    base = tmp_path / "detector_nightly"
    _write_local(base)

    resp = _client(monkeypatch).get(
        "/api/activity/hexes",
        params={"start": "2024-05-17", "end": "2024-05-18", "nightly_dir": str(base)},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert [
        (r["h3_index"], r["raw_count_sum"], r["detector_nights"]) for r in data
    ] == [(HEX_WEST, 60, 1)]


def test_nights(monkeypatch: Any, tmp_path: Path) -> None:
    base = tmp_path / "detector_nightly"
    _write_local(base)

    resp = _client(monkeypatch).get(
        "/api/activity/nights", params={"nightly_dir": str(base)}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["min_night"] == "2024-05-16"
    assert body["max_night"] == "2024-09-02"
    assert [
        (n["night"], n["raw_count_sum"], n["detector_nights"], n["n_hexes"])
        for n in body["nights"]
    ] == [
        ("2024-05-16", 40, 2, 1),
        ("2024-05-17", 60, 1, 1),
        ("2024-09-02", 10, 1, 1),
    ]


def test_survey_windows(monkeypatch: Any, tmp_path: Path) -> None:
    base = tmp_path / "detector_nightly"
    _write_local(base)

    resp = _client(monkeypatch).get(
        "/api/activity/survey_windows", params={"nightly_dir": str(base)}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert [
        (r["h3_index"], r["first_night"], r["last_night"], r["n_nights"]) for r in data
    ] == [
        (HEX_WEST, "2024-05-16", "2024-05-17", 2),
        (HEX_EAST, "2024-09-02", "2024-09-02", 1),
    ]
    assert data[0]["detector_nights"] == 3
    assert data[0]["raw_count_sum"] == 100


def test_missing_dir_is_empty(monkeypatch: Any, tmp_path: Path) -> None:
    client = _client(monkeypatch)
    params = {"nightly_dir": str(tmp_path / "missing")}

    assert client.get("/api/activity/hexes", params=params).json() == []
    assert client.get("/api/activity/survey_windows", params=params).json() == []
    assert client.get("/api/activity/nights", params=params).json() == {
        "nights": [],
        "min_night": None,
        "max_night": None,
    }


def test_hexes_read_from_s3(monkeypatch: Any) -> None:
    prefix = "analytics/detector_nightly"
    buf = BytesIO()
    _night(date(2024, 5, 16), [(HEX_WEST, "1001", 3)]).to_parquet(buf, index=False)
    objects = {f"{prefix}/detector_nightly_2024-05-16.parquet": buf.getvalue()}

    def _fake_list_keys(prefix_arg: str) -> Iterable[str]:
        return [k for k in objects if k.startswith(prefix_arg)]

    monkeypatch.setenv("HEATMAP_ACTIVITY_SOURCE", "s3")
    monkeypatch.setattr("app.main.list_keys", _fake_list_keys)
    monkeypatch.setattr("app.main.get_object_bytes", objects.__getitem__)
    main._object_cache.clear()

    resp = TestClient(app).get(
        "/api/activity/hexes",
        params={"start": "2024-05-16", "end": "2024-05-17", "nightly_dir": prefix},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["raw_count_sum"] == 3
    assert data[0]["detector_nights"] == 1
