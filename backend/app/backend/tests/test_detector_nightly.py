"""Tests for the detector-nightly survey transform (in memory, no database)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import h3
import pandas as pd

from app.backend.eti.transform.detector_nightly import (
    OUTPUT_COLUMNS,
    DetectorNightlyConfig,
    assign_night,
    build_detector_nightly,
)

# A MAUG deployment point; any fixed coordinate works.
LAT, LON = 51.75235, -9.28930


@dataclass
class DummySample:
    """Stand-in for ``MaugSummarySample`` with only the fields the transform reads."""

    timestamp_utc: datetime
    files_count: Optional[int]
    detector_serial: Optional[str]
    source_folder: Optional[str] = None
    lat: float = LAT
    lon: float = LON


def test_assign_night_noon_to_noon() -> None:
    """Evening and following early morning share a night; noon starts the next one."""

    stamps = pd.Series(
        pd.to_datetime(
            [
                "2024-05-15 20:00",  # evening -> night of 15th
                "2024-05-16 03:00",  # after midnight -> still night of 15th
                "2024-05-16 11:59",  # just before noon -> night of 15th
                "2024-05-16 12:00",  # noon -> night of 16th
            ]
        )
    )

    nights = assign_night(stamps, night_start_hour=12).dt.strftime("%Y-%m-%d")

    assert nights.tolist() == ["2024-05-15", "2024-05-15", "2024-05-15", "2024-05-16"]


def test_one_row_per_night_site_detector() -> None:
    """Samples collapse to one survey per detector per night, summing files."""

    samples = [
        DummySample(datetime(2024, 5, 15, 21, 0), 2, "1397"),
        DummySample(datetime(2024, 5, 16, 2, 0), 3, "1397"),
        DummySample(datetime(2024, 5, 15, 21, 0), 0, "4050"),
        DummySample(datetime(2024, 5, 16, 21, 0), 1, "1397"),
    ]

    table = build_detector_nightly(samples)

    assert list(table.columns) == OUTPUT_COLUMNS
    assert len(table) == 3
    first = table[
        (table["detector_serial"] == "1397") & (table["night"] == "2024-05-15")
    ]
    assert first["raw_count_sum"].item() == 5
    assert first["sample_count"].item() == 2
    silent = table[table["detector_serial"] == "4050"]
    assert silent["raw_count_sum"].item() == 0
    assert table["h3_index"].unique().tolist() == [h3.latlng_to_cell(LAT, LON, 10)]


def test_mid_night_card_swap_is_one_survey() -> None:
    """3591 swapped A -> B at 03:45; a folder change mid-night still yields one row."""

    samples = [
        DummySample(datetime(2024, 5, 15, 22, 0), 4, "3591", source_folder=None),
        DummySample(datetime(2024, 5, 16, 3, 44), 1, "3591", source_folder=None),
        DummySample(datetime(2024, 5, 16, 3, 45), 2, "3591", source_folder="special"),
    ]

    table = build_detector_nightly(samples)

    assert len(table) == 1
    assert table["raw_count_sum"].item() == 7
    assert table["source_folder"].item() == "special"


def test_folder_flag_kept_and_top_level_is_none() -> None:
    """Flagged detectors keep their folder; top-level detectors get None."""

    samples = [
        DummySample(datetime(2024, 9, 24, 21, 0), 1, "4074", source_folder="NA"),
        DummySample(datetime(2024, 9, 24, 21, 0), 1, "4129", source_folder=None),
    ]

    table = build_detector_nightly(samples).set_index("detector_serial")

    assert table.loc["4074", "source_folder"] == "NA"
    assert table.loc["4129", "source_folder"] is None


def test_samples_without_serial_are_dropped() -> None:
    """Rows with no detector serial cannot be a survey and are excluded."""

    samples = [
        DummySample(datetime(2024, 5, 16, 20, 0), 3, None, lat=54.0, lon=-7.0),
        DummySample(datetime(2024, 5, 16, 21, 0), 1, "7093"),
    ]

    table = build_detector_nightly(samples)

    assert table["detector_serial"].tolist() == ["7093"]


def test_resolution_is_configurable() -> None:
    """A coarser site resolution changes the hex, not the survey count."""

    samples = [DummySample(datetime(2024, 5, 16, 21, 0), 1, "7093")]

    table = build_detector_nightly(samples, DetectorNightlyConfig(resolution=7))

    assert table["h3_index"].item() == h3.latlng_to_cell(LAT, LON, 7)


def test_empty_input_gives_empty_table() -> None:
    """No samples produce an empty frame with the output schema."""

    table = build_detector_nightly([])

    assert table.empty
    assert list(table.columns) == OUTPUT_COLUMNS
