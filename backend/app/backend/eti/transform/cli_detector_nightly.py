"""CLI entrypoint for the detector-nightly transform.

Example (inside the ``api`` container)::

    uv run python -m app.backend.eti.transform.cli_detector_nightly \
        /data/analytics/detector_nightly --start 2024-05-07
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .cli_h3_analytics import _parse_dt
from .detector_nightly import (
    NIGHT_START_HOUR,
    SITE_H3_RESOLUTION,
    DetectorNightlyConfig,
    run_detector_nightly,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write one survey row per night x H3 site x detector serial",
    )
    parser.add_argument(
        "output_dir", type=Path, help="Directory for detector_nightly Parquet files"
    )
    parser.add_argument("--start", type=str, default=None, help="Inclusive start")
    parser.add_argument("--end", type=str, default=None, help="Exclusive end")
    parser.add_argument(
        "--resolution",
        type=int,
        default=SITE_H3_RESOLUTION,
        help=f"H3 resolution that defines a site (default: {SITE_H3_RESOLUTION})",
    )
    parser.add_argument(
        "--night-start-hour",
        type=int,
        default=NIGHT_START_HOUR,
        help=f"Hour a survey night rolls over (default: {NIGHT_START_HOUR})",
    )
    return parser.parse_args()


def main() -> None:
    """Parse arguments, run the transform, and report the output folder."""

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    args = _parse_args()
    config = DetectorNightlyConfig(
        resolution=args.resolution, night_start_hour=args.night_start_hour
    )
    output_dir = run_detector_nightly(
        start=_parse_dt(args.start),
        end=_parse_dt(args.end),
        output_dir=args.output_dir,
        config=config,
    )
    print(f"Wrote detector-nightly Parquet files to {output_dir}")


if __name__ == "__main__":  # pragma: no cover - convenience entrypoint
    main()
