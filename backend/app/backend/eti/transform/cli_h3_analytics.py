"""CLI entrypoint for running H3 × time analytics.

Example usage (from within the `api` container):

    uv run python -m app.backend.eti.transform.cli_h3_analytics \
        --start "2024-05-16" \
        --end "2024-05-17" \
        --resolution 7 \
        /data/analytics/h3_daily

This will read `MaugSummarySample` rows from Postgres, aggregate them into H3
cells per day, and write partitioned Parquet files under the given output
folder.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .h3_analytics import H3AnalyticsConfig, run_h3_analytics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run H3 × time analytics")
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory to write partitioned H3 analytics Parquet files",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Inclusive start timestamp (UTC), e.g. 2024-05-16 or 2024-05-16T00:00:00",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Exclusive end timestamp (UTC)",
    )
    parser.add_argument(
        "--resolution",
        type=int,
        default=7,
        help="H3 resolution (default: 7)",
    )
    parser.add_argument(
        "--time-freq",
        type=str,
        default="1D",
        help="Pandas time frequency for binning (default: 1D)",
    )
    return parser.parse_args()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None

    # Accept bare dates and full ISO timestamps.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    msg = (
        f"Could not parse datetime '{value}'. "
        "Expected YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS]."
    )
    raise SystemExit(msg)


def main() -> None:
    args = _parse_args()

    start_dt = _parse_dt(args.start)
    end_dt = _parse_dt(args.end)

    config = H3AnalyticsConfig(resolution=args.resolution, time_freq=args.time_freq)

    output_dir = run_h3_analytics(
        start=start_dt,
        end=end_dt,
        output_dir=args.output_dir,
        config=config,
    )

    print(f"Wrote H3 analytics Parquet files to {output_dir}")


if __name__ == "__main__":  # pragma: no cover - convenience entrypoint
    main()
