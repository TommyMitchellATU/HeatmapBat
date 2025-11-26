from __future__ import annotations

import os
from typing import BinaryIO

import boto3
from botocore.client import Config


def get_s3_client():
    """Create and return a configured S3 client.

    Uses environment variables for endpoint URL and credentials, which makes it
    compatible with both real AWS S3 and S3-compatible services like MinIO.
    """
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def get_bucket_name() -> str:
    """Return the target S3 bucket name from environment or a default."""
    return os.environ.get("S3_BUCKET", "heatmapbat")


def upload_fileobj(obj: BinaryIO, key: str) -> None:
    """Upload a file-like object to S3.

    Args:
        obj: Open binary file-like object positioned at the start of the data.
        key: Destination object key (path/name) within the bucket.
    """
    client = get_s3_client()
    client.upload_fileobj(obj, get_bucket_name(), key)


def download_fileobj(key: str, obj: BinaryIO) -> None:
    """Download an S3 object into a file-like object.

    Args:
        key: Source object key in the S3 bucket.
        obj: Open binary file-like object to write the downloaded data into.
    """
    client = get_s3_client()
    client.download_fileobj(get_bucket_name(), key, obj)
