from __future__ import annotations

from pathlib import Path

from app.backend.eti.s3 import upload_fileobj


def upload_csv(path: Path, key: str) -> None:
    with path.open("rb") as f:
        upload_fileobj(f, key)


def main() -> None:
    # Default locations mirror the current docker-compose mounts.
    csv_path = Path("maug_summary_samples_combined.csv")
    key = "maug_summary_samples_combined.csv"
    upload_csv(csv_path, key)
    print(f"Uploaded {csv_path} to {key} in configured bucket")


if __name__ == "__main__":
    main()
