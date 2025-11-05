"""ETL pipeline orchestration entrypoint (placeholder).

Design intent:
- Provide a single callable to coordinate extract → transform → load steps.
- Keep I/O (filesystem/S3) and compute (pandas/pyarrow) isolated from the API layer.
- Make it trivial to schedule later (e.g., with Prefect, cron, or Celery).
"""

from pathlib import Path


def run_etl(input_dir: Path, output_dir: Path) -> None:
    """
    Placeholder ETL entrypoint.
    - Read raw files from input_dir
    - Clean/normalize
    - Write partitioned Parquet to output_dir

    Why this is minimal:
    - Demonstrates the I/O contract early (inputs/outputs) so call sites and
        tests can be written before heavy logic exists.
    - Lets infra (permissions, mounts, buckets) be validated independently of
        domain logic.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # TODO: implement extraction/transform/load
    print(f"ETL ran (placeholder). Read from {input_dir}, wrote to {output_dir}")
