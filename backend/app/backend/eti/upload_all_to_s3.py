"""Upload all data files to MinIO/S3 for production use.

This script uploads:
1. GeoJSON/CSV exports (for /api/heatmap/points endpoint)
2. H3 Parquet analytics (for /api/heatmap/h3_parquet endpoint)

After running this, set HEATMAP_SOURCE=s3 to use MinIO instead of local files.

Usage:
    docker compose exec api uv run python -m app.backend.eti.upload_all_to_s3
"""

from __future__ import annotations

import os
from pathlib import Path

from app.backend.eti.s3 import ensure_bucket_exists, upload_fileobj, get_bucket_name


def upload_file(local_path: Path, s3_key: str) -> None:
    """Upload a single file to S3."""
    with local_path.open("rb") as f:
        upload_fileobj(f, s3_key)
    print(f"  ✓ {local_path.name} → {s3_key}")


def main() -> None:
    """Upload all data files to MinIO."""
    
    # Ensure bucket exists
    print(f"Ensuring bucket '{get_bucket_name()}' exists...")
    ensure_bucket_exists()
    
    # Base paths (inside container)
    data_root = Path("/data")
    exports_dir = data_root / "exports"
    analytics_dir = data_root / "analytics" / "h3_daily"
    
    uploaded = 0
    
    # Upload exports (GeoJSON, CSV)
    print("\n📁 Uploading exports...")
    if exports_dir.exists():
        for path in exports_dir.glob("*"):
            if path.is_file() and path.suffix in (".geojson", ".csv"):
                s3_key = f"data/exports/{path.name}"
                upload_file(path, s3_key)
                uploaded += 1
    else:
        print(f"  ⚠ Exports directory not found: {exports_dir}")
    
    # Upload H3 Parquet analytics
    print("\n📁 Uploading H3 analytics...")
    if analytics_dir.exists():
        parquet_files = list(analytics_dir.glob("*.parquet"))
        for path in parquet_files:
            s3_key = f"data/analytics/h3_daily/{path.name}"
            upload_file(path, s3_key)
            uploaded += 1
    else:
        print(f"  ⚠ Analytics directory not found: {analytics_dir}")
    
    # Upload combined CSV (used by some endpoints)
    print("\n📁 Uploading combined CSV...")
    combined_csv = data_root / "maug_summary_samples_combined.csv"
    if combined_csv.exists():
        upload_file(combined_csv, "data/maug_summary_samples_combined.csv")
        uploaded += 1
    
    points_csv = data_root / "maug_points.csv"
    if points_csv.exists():
        upload_file(points_csv, "data/maug_points.csv")
        uploaded += 1
    
    print(f"\n✅ Uploaded {uploaded} files to MinIO bucket '{get_bucket_name()}'")
    print("\nTo use S3 mode, add to .env or docker-compose.yml:")
    print("  HEATMAP_SOURCE=s3")
    print("\nOr per-endpoint:")
    print("  HEATMAP_POINTS_SOURCE=s3")
    print("  HEATMAP_H3_SOURCE=s3")


if __name__ == "__main__":
    main()
