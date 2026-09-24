"""Tests for summary-file discovery, serial parsing and folder flags (no database)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.backend.eti.extract.summary_import import (
    derive_source_folder,
    find_summary_files,
    parse_detector_serial,
    parse_summary_file,
)

HEADER = "DATE,TIME,LAT,NS,LON,EW,POWER(V),TEMP(C),#FILES,#SCRUBBED,MIC0 TYPE \n"


def _write_summary(path: Path, rows: list[str]) -> Path:
    """Write a minimal summary file with the real detector header."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "serial"),
    [
        ("D01-MEEN-6771_A_Summary.txt", "6771"),
        ("MEEN-3505_A_Summary.txt", "3505"),
        ("D01-GANN-3591_B_Summary.txt", "3591"),
        ("MAUG-0031_A_Summary.txt", "0031"),
        ("notes_Summary.txt", None),
    ],
)
def test_parse_detector_serial(name: str, serial: str | None) -> None:
    """The serial is the number before the card letter; the D0x prefix is ignored."""

    assert parse_detector_serial(Path(name)) == serial


def test_derive_source_folder(tmp_path: Path) -> None:
    """Top-level files get None; nested files get their posix folder path."""

    assert derive_source_folder(tmp_path / "a_Summary.txt", tmp_path) is None
    assert derive_source_folder(tmp_path / "NA" / "a_Summary.txt", tmp_path) == "NA"
    nested = tmp_path / "special" / "D06" / "a_Summary.txt"
    assert derive_source_folder(nested, tmp_path) == "special/D06"


def test_find_summary_files_is_recursive(tmp_path: Path) -> None:
    """Files in NA/ and special/D06/ are found alongside top-level ones."""

    top = _write_summary(tmp_path / "MEEN-1234_A_Summary.txt", [])
    na = _write_summary(tmp_path / "NA" / "GANN-4074_A_Summary.txt", [])
    d06 = _write_summary(tmp_path / "special" / "D06" / "MAUG-3509_A_Summary.txt", [])
    (tmp_path / "readme.txt").write_text("not a summary", encoding="utf-8")

    assert set(find_summary_files(tmp_path)) == {top, na, d06}


def test_parse_summary_file_sets_serial_and_folder(tmp_path: Path) -> None:
    """Parsed rows carry the serial from the filename and the folder flag given."""

    path = _write_summary(
        tmp_path / "special" / "D01-MEEN-3505_A_Summary.txt",
        [
            "2024-Sep-12,19:25:59,54.73791,n,7.92222,w,6.5,15.75,2,0,U2",
            "2024-Sep-12,19:26:59,54.73791,n,7.92222,w,6.5,15.50,0,0,U2",
        ],
    )

    samples = parse_summary_file(path, source_folder="special")

    assert len(samples) == 2
    assert {s.detector_serial for s in samples} == {"3505"}
    assert {s.source_folder for s in samples} == {"special"}
    assert samples[0].site_id == "D01"
    assert samples[0].lon == pytest.approx(-7.92222)
    assert [s.files_count for s in samples] == [2, 0]
