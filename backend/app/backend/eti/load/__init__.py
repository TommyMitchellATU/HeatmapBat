"""Loaders: Parquet writers, PostGIS upserts, S3 uploads.

Rationale:
- Keep side effects (db writes, object storage) isolated behind simple adapters.
- Make it easy to switch destinations (e.g., local FS vs S3) with minimal changes.
"""
