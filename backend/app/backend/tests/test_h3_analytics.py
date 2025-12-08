"""Tests for the H3 × time analytics transform.

These tests exercise the in-memory pieces of the analytics pipeline without
hitting the real database or filesystem. The goal is to ensure that:

* samples are converted into a DataFrame correctly,
* H3 indices and time bins are attached as expected, and
* aggregation over H3 × time buckets behaves as designed.
"""

from __future__ import annotations

from datetime import datetime

import h3

from app.backend.eti.transform.h3_analytics import (
    H3AnalyticsConfig,
    _aggregate,
    _attach_h3_and_time_bins,
    _samples_to_dataframe,
)


class DummySample:
    """Simple stand-in for ``MaugSummarySample``.

    Only the fields used by the transform are included here so the tests
    remain lightweight and do not depend on the ORM or a running database.
    """

    def __init__(
        self,
        *,
        timestamp_utc: datetime,
        lat: float,
        lon: float,
        files_count: int,
        site_id: str | None = None,
    ) -> None:
        self.timestamp_utc = timestamp_utc
        self.lat = lat
        self.lon = lon
        self.files_count = files_count
        self.site_id = site_id


def test_samples_to_dataframe_basic_roundtrip() -> None:
    """A couple of dummy samples produce the expected DataFrame columns."""

    ts = datetime(2024, 5, 16, 21, 0, 0)
    samples = [
        DummySample(timestamp_utc=ts, lat=54.0, lon=-7.0, files_count=2, site_id="A"),
        DummySample(timestamp_utc=ts, lat=54.1, lon=-7.1, files_count=3, site_id="B"),
    ]

    df = _samples_to_dataframe(samples)

    assert list(df.columns) == [
        "timestamp_utc",
        "lat",
        "lon",
        "raw_count",
        "site_id",
    ]
    assert len(df) == 2
    assert df["raw_count"].tolist() == [2, 3]
    assert df["site_id"].tolist() == ["A", "B"]


def test_attach_h3_and_time_bins_and_aggregate() -> None:
    """End-to-end in-memory check of H3 + time aggregation.

    Two samples in the same H3 cell and time bin should collapse to a single
    aggregated record with the correct summed counts and sample counter.
    """

    base_ts = datetime(2024, 5, 16, 21, 30, 0)

    # Two points very close together so they share a cell at this resolution.
    samples = [
        DummySample(timestamp_utc=base_ts, lat=54.0, lon=-7.0, files_count=1, site_id="A"),
        DummySample(timestamp_utc=base_ts, lat=54.0001, lon=-7.0001, files_count=2, site_id="A"),
    ]

    config = H3AnalyticsConfig(resolution=7, time_freq="1H")

    df = _samples_to_dataframe(samples)
    df = _attach_h3_and_time_bins(df, config)

    # Both rows should share the same H3 index and time_bin_start.
    assert "h3_index" in df.columns
    assert "time_bin_start" in df.columns
    assert len(df["h3_index"].unique()) == 1
    assert len(df["time_bin_start"].unique()) == 1

    # Sanity check that our understanding of the H3 function matches the
    # library behaviour.
    expected_index = h3.latlng_to_cell(54.0, -7.0, config.resolution)
    assert df["h3_index"].iloc[0] == expected_index

    agg = _aggregate(df)

    # A single aggregated record for this H3 × time × site combination.
    assert len(agg) == 1
    row = agg.iloc[0]
    assert row["raw_count_sum"] == 3  # 1 + 2
    assert row["sample_count"] == 2
    assert row["site_id"] == "A"

