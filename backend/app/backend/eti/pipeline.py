from pathlib import Path


def run_etl(input_dir: Path, output_dir: Path) -> None:
    """
    Placeholder ETL entrypoint.
    - Read raw files from input_dir
    - Clean/normalize
    - Write partitioned Parquet to output_dir
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # TODO: implement extraction/transform/load
    print(f"ETL ran (placeholder). Read from {input_dir}, wrote to {output_dir}")
