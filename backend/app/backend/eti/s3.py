from __future__ import annotations

import os
from typing import BinaryIO

import boto3
from botocore.client import Config


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def get_bucket_name() -> str:
    return os.environ.get("S3_BUCKET", "heatmapbat")


def upload_fileobj(obj: BinaryIO, key: str) -> None:
    client = get_s3_client()
    client.upload_fileobj(obj, get_bucket_name(), key)


def download_fileobj(key: str, obj: BinaryIO) -> None:
    client = get_s3_client()
    client.download_fileobj(get_bucket_name(), key, obj)
