from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, Iterable

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app


class _FakeS3:
    def __init__(self) -> None:
        self._objects: Dict[str, bytes] = {}

    def put_object(
        self, Bucket: str, Key: str, Body: bytes
    ) -> None:  # pragma: no cover - helper
        self._objects[f"{Bucket}/{Key}"] = Body

    def list_objects_v2(
        self, Bucket: str, Prefix: str, ContinuationToken: str | None = None
    ) -> Dict[str, Any]:
        keys = [
            key.split("/", 1)[1]
            for key in self._objects
            if key.startswith(f"{Bucket}/{Prefix}")
        ]
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:
        data = self._objects[f"{Bucket}/{Key}"]
        return {"Body": BytesIO(data)}


def test_h3_parquet_reads_from_s3(monkeypatch: Any, tmp_path: Any) -> None:
    bucket = "heatmapbat"
    prefix = "analytics/h3_daily"

    # Prepare a fake parquet partition and upload to fake S3.
    df = pd.DataFrame(
        {
            "h3_index": ["8928308280fffff"],
            "raw_count_sum": [3.0],
            "sample_count": [2],
        }
    )
    parquet_bytes = BytesIO()
    df.to_parquet(parquet_bytes, index=False)

    fake = _FakeS3()
    fake.put_object(
        Bucket=bucket,
        Key=f"{prefix}/h3_analytics_2024-05-16.parquet",
        Body=parquet_bytes.getvalue(),
    )

    # Monkeypatch S3 helpers used by the endpoint.
    monkeypatch.setenv("HEATMAP_H3_SOURCE", "s3")
    monkeypatch.setenv("S3_BUCKET", bucket)

    def _fake_client() -> _FakeS3:
        return fake

    def _fake_list_keys(prefix_arg: str) -> Iterable[str]:
        resp = fake.list_objects_v2(Bucket=bucket, Prefix=prefix_arg)
        return [item["Key"] for item in resp.get("Contents", [])]

    def _fake_get_object_bytes(key: str) -> bytes:
        return fake.get_object(Bucket=bucket, Key=key)["Body"].read()

    monkeypatch.setenv("HEATMAP_H3_SOURCE", "s3")
    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setattr("app.backend.eti.s3.get_s3_client", _fake_client)
    monkeypatch.setattr("app.main.list_keys", _fake_list_keys)
    monkeypatch.setattr("app.main.get_object_bytes", _fake_get_object_bytes)

    client = TestClient(app)
    resp = client.get(
        "/api/heatmap/h3_parquet",
        params={"start": "2024-05-16", "end": "2024-05-17", "analytics_dir": prefix},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["raw_count_sum"] == 3.0
    assert row["sample_count"] == 2
    assert "lat" in row and "lon" in row
