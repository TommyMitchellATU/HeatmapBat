"""ETL pipeline orchestration entrypoint.

Coordinates extract → transform → load steps:
1. Extract: Import detector *_Summary.txt files into PostGIS
2. Transform: Generate H3 spatial aggregates as Parquet
3. Load: Optionally export to CSV/GeoJSON for external use

Run via CLI:
    uv run python -m app.backend.eti.pipeline /data /data/analytics/h3_daily

Or import and call directly:
    from app.backend.eti.pipeline import run_etl
    run_etl(input_dir, output_dir, export_csv=True)
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.backend.eti.db import SessionLocal
from app.backend.eti.extract.summary_import import load_summary_file
from app.backend.eti.load.export import export_samples_to_csv
from app.backend.eti.load.geojson_export import export_samples_to_geojson
from app.backend.eti.transform.h3_analytics import H3AnalyticsConfig, run_h3_analytics

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Summary of a pipeline run."""

    files_imported: int
    rows_imported: int
    h3_output_dir: Optional[Path]
    csv_path: Optional[Path]
    geojson_path: Optional[Path]


def run_etl(
    input_dir: Path,
    output_dir: Path,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    h3_resolution: int = 7,
    export_csv: bool = False,
    export_geojson: bool = False,
    skip_import: bool = False,
    skip_h3: bool = False,
) -> PipelineResult:
    """Run the full ETL pipeline.

    Args:
        input_dir: Directory containing detector *_Summary.txt files.
        output_dir: Base directory for outputs (H3 Parquet, exports).
        start: Optional start timestamp filter for analytics/exports.
        end: Optional end timestamp filter for analytics/exports.
        h3_resolution: H3 resolution for spatial binning (default 7).
        export_csv: If True, also export samples to CSV.
        export_geojson: If True, also export samples to GeoJSON.
        skip_import: If True, skip the import step (use existing DB data).
        skip_h3: If True, skip H3 analytics generation.

    Returns:
        PipelineResult with counts and output paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    files_imported = 0
    rows_imported = 0
    h3_output: Optional[Path] = None
    csv_path: Optional[Path] = None
    geojson_path: Optional[Path] = None

    # ── Step 1: Extract (import detector files to DB) ──
    if not skip_import:
        summary_files = list(input_dir.glob("*_Summary.txt"))
        if summary_files:
            logger.info(
                "Importing %d summary files from %s", len(summary_files), input_dir
            )
            db = SessionLocal()
            try:
                for path in summary_files:
                    try:
                        count = load_summary_file(db, path)
                        rows_imported += count
                        files_imported += 1
                        logger.info("  Imported %d rows from %s", count, path.name)
                    except Exception as exc:
                        logger.warning("  Failed to import %s: %s", path.name, exc)
            finally:
                db.close()
            logger.info(
                "Import complete: %d files, %d total rows",
                files_imported,
                rows_imported,
            )
        else:
            logger.info("No *_Summary.txt files found in %s", input_dir)

    # ── Step 2: Transform (H3 analytics) ──
    if not skip_h3:
        h3_output = output_dir / "h3_daily"
        h3_output.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Generating H3 analytics (resolution=%d) → %s", h3_resolution, h3_output
        )

        config = H3AnalyticsConfig(resolution=h3_resolution, time_freq="1D")
        run_h3_analytics(start=start, end=end, output_dir=h3_output, config=config)

        parquet_count = len(list(h3_output.glob("h3_analytics_*.parquet")))
        logger.info("H3 analytics complete: %d Parquet files", parquet_count)

    # ── Step 3: Load (optional exports) ──
    if export_csv or export_geojson:
        exports_dir = output_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        db = SessionLocal()
        try:
            if export_csv:
                csv_path = exports_dir / "maug_points.csv"
                count = export_samples_to_csv(db, csv_path, start=start, end=end)
                logger.info("Exported %d rows to %s", count, csv_path)

            if export_geojson:
                geojson_path = exports_dir / "maug_points.geojson"
                count = export_samples_to_geojson(
                    db, geojson_path, start=start, end=end
                )
                logger.info("Exported %d features to %s", count, geojson_path)
        finally:
            db.close()

    return PipelineResult(
        files_imported=files_imported,
        rows_imported=rows_imported,
        h3_output_dir=h3_output,
        csv_path=csv_path,
        geojson_path=geojson_path,
    )


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise SystemExit(
        f"Could not parse datetime '{value}'. Use YYYY-MM-DD or ISO format."
    )


def main() -> None:
    """CLI entrypoint for the ETL pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Run HeatmapBat ETL pipeline: import → H3 analytics → export",
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing detector *_Summary.txt files",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Base output directory for H3 Parquet and exports",
    )
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--resolution", type=int, default=7, help="H3 resolution (default: 7)"
    )
    parser.add_argument("--csv", action="store_true", help="Also export to CSV")
    parser.add_argument("--geojson", action="store_true", help="Also export to GeoJSON")
    parser.add_argument(
        "--skip-import", action="store_true", help="Skip file import step"
    )
    parser.add_argument("--skip-h3", action="store_true", help="Skip H3 analytics step")

    args = parser.parse_args()

    result = run_etl(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        start=_parse_dt(args.start),
        end=_parse_dt(args.end),
        h3_resolution=args.resolution,
        export_csv=args.csv,
        export_geojson=args.geojson,
        skip_import=args.skip_import,
        skip_h3=args.skip_h3,
    )

    print("\n── Pipeline Summary ──")
    print(f"Files imported: {result.files_imported}")
    print(f"Rows imported:  {result.rows_imported}")
    if result.h3_output_dir:
        print(f"H3 Parquet:     {result.h3_output_dir}")
    if result.csv_path:
        print(f"CSV export:     {result.csv_path}")
    if result.geojson_path:
        print(f"GeoJSON export: {result.geojson_path}")


if __name__ == "__main__":
    main()
