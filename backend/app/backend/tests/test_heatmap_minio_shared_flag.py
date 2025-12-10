from __future__ import annotations

import json
from io import BytesIO
from typing import Any, Dict, Iterable

import pandas as pd
from fastapi.testclient import TestClient

from app import main
from app.main import app


class _FakeS3:
    def __init__(self) -> None:
        self._objects: Dict[str, bytes] = {}

    def put_object(self, bucket: str, key: str, body: bytes) -> None:
        self._objects[f"{bucket}/{key}"] = body

    def list_objects_v2(
        self, Bucket: str, Prefix: str, ContinuationToken: str | None = None
    ) -> Dict[str, Any]:
        keys = [
            k.split("/", 1)[1]
            for k in self._objects
            if k.startswith(f"{Bucket}/{Prefix}")
        ]
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:
        data = self._objects[f"{Bucket}/{Key}"]
        return {"Body": BytesIO(data)}


def test_shared_flag_reads_points_and_h3_from_s3(monkeypatch: Any) -> None:
    bucket = "heatmapbat"
    points_key = "exports/maug_points.geojson"
    h3_prefix = "analytics/h3_daily"
    h3_key = f"{h3_prefix}/h3_analytics_2024-05-16.parquet"

    fake = _FakeS3()

    # Points GeoJSON
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-7.0, 54.0]},
                "properties": {"timestamp_utc": "2024-05-16T20:00:00", "raw_count": 2},
            }
        ],
    }
    fake.put_object(bucket, points_key, json.dumps(geojson).encode())

    # H3 Parquet
    df = pd.DataFrame(
        {
            "h3_index": ["8928308280fffff"],
            "raw_count_sum": [3.0],
            "sample_count": [2],
        }
    )
    buf = BytesIO()
    df.to_parquet(buf, index=False)
    fake.put_object(bucket, h3_key, buf.getvalue())

    # Monkeypatch shared helpers
    def _fake_list_keys(prefix: str) -> Iterable[str]:
        resp = fake.list_objects_v2(Bucket=bucket, Prefix=prefix)
        return [item["Key"] for item in resp.get("Contents", [])]

    def _fake_get_object_bytes(key: str) -> bytes:
        return fake.get_object(Bucket=bucket, Key=key)["Body"].read()

    monkeypatch.setenv("HEATMAP_SOURCE", "s3")
    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setattr("app.main.list_keys", _fake_list_keys)
    monkeypatch.setattr("app.main.get_object_bytes", _fake_get_object_bytes)
    # Clear cache to avoid cross-test pollution
    main._object_cache.clear()

    client = TestClient(app)

    # Points
    points_resp = client.get(
        "/api/heatmap/points",
        params={
            "start": "2024-05-16T00:00:00",
            "end": "2024-05-17T00:00:00",
            "points_object": points_key,
        },
    )
    assert points_resp.status_code == 200
    points = points_resp.json()
    assert len(points) == 1
    assert points[0]["raw_count"] == 2

    # H3
    h3_resp = client.get(
        "/api/heatmap/h3_parquet",
        params={"start": "2024-05-16", "end": "2024-05-17", "analytics_dir": h3_prefix},
    )
    assert h3_resp.status_code == 200
    hexes = h3_resp.json()
    assert len(hexes) == 1
    assert hexes[0]["raw_count_sum"] == 3.0
    assert hexes[0]["sample_count"] == 2
