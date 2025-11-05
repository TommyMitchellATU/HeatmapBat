"""ETL package for ingesting, transforming, and loading bat detection datasets.

What lives here:
- extract/: Readers for raw sources (summary TXT/CSV, WAV header sampling, metadata)
- transform/: Cleaning, timezone normalization, geotagging, feature engineering
- load/: Parquet writers, PostGIS upserts, S3 object writes
- pipeline.py: An orchestration entrypoint (placeholder now, schedulable later)

Why it exists now (even as a scaffold):
- Keeps domain concerns isolated from the web API while the pipeline evolves.
- Lets us wire storage and computation incrementally (S3/PostGIS/Redis).
"""
