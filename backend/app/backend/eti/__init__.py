"""
ETL package for ingesting acoustic detections and metadata.

Structure:
- extract/: readers for raw files (e.g., summary TXT/CSV, WAV header sampling)
- transform/: cleaning, timezone normalization, feature engineering
- load/: writing Parquet, PostGIS upserts, S3 object writes
- pipeline.py: orchestration entrypoint (later with Prefect)
"""
