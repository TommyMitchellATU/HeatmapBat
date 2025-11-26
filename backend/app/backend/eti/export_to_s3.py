from __future__ import annotations

from pathlib import Path

from app.backend.eti.s3 import upload_fileobj


def upload_csv(path: Path, key: str) -> None:
    """Upload a local CSV file to S3 using the given object key.

    Args:
        path: Filesystem path to the CSV file to upload.
        key: Destination object key (path/name) in the S3 bucket.
    """
    # Open the file in binary mode so it can be streamed to S3.
    with path.open("rb") as f:
        # Delegate the actual upload to the shared S3 helper.
        upload_fileobj(f, key)


def main() -> None:
    """Entry point: upload the combined MAUG summary CSV to S3."""
    # CSV is expected in the repo root's data/ directory, mounted at /data
    # inside the container (see docker-compose bind mount).
    csv_path = Path("/data/maug_summary_samples_combined.csv")

    # Use a simple key name in the configured S3 bucket.
    key = "maug_summary_samples_combined.csv"

    # Perform the upload.
    upload_csv(csv_path, key)

    # Log a simple confirmation message to stdout.
    print(f"Uploaded {csv_path} to {key} in configured bucket")


if __name__ == "__main__":
    # Allow running this module directly as a script.
    main()
