from __future__ import annotations

import json
from io import BytesIO
from typing import Any, Dict

from fastapi.testclient import TestClient

from app.main import app


class _FakeS3:
    def __init__(self) -> None:
        self._objects: Dict[str, bytes] = {}

    def put_geojson(
        self, bucket: str, key: str, features: list[dict[str, Any]]
    ) -> None:
        payload = {"type": "FeatureCollection", "features": features}
        self._objects[f"{bucket}/{key}"] = json.dumps(payload).encode()

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:
        data = self._objects[f"{Bucket}/{Key}"]
        return {"Body": BytesIO(data)}


def test_heatmap_points_reads_geojson_from_s3(monkeypatch: Any) -> None:
    bucket = "heatmapbat"
    key = "exports/maug_points.geojson"

    fake = _FakeS3()
    fake.put_geojson(
        bucket,
        key,
        [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-7.0, 54.0]},
                "properties": {
                    "timestamp_utc": "2024-05-16T20:00:00",
                    "raw_count": 2,
                },
            }
        ],
    )

    def _fake_get_object_bytes(target_key: str) -> bytes:
        return fake.get_object(Bucket=bucket, Key=target_key)["Body"].read()

    monkeypatch.setenv("HEATMAP_POINTS_SOURCE", "s3")
    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setattr("app.main.get_object_bytes", _fake_get_object_bytes)

    client = TestClient(app)
    resp = client.get(
        "/api/heatmap/points",
        params={
            "start": "2024-05-16T00:00:00",
            "end": "2024-05-17T00:00:00",
            "points_object": key,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["raw_count"] == 2
    assert data[0]["lat"] == 54.0
    assert data[0]["lon"] == -7.0
    assert data[0]["timestamp_utc"].startswith("2024-05-16")
