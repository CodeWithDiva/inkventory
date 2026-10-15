"""File storage for uploaded catalogs and export files: local disk in dev, S3 in production."""

from pathlib import Path

from app.config import get_settings


def save_file(key: str, data: bytes) -> str:
    settings = get_settings()
    if settings.storage_backend == "s3":
        import boto3

        boto3.client("s3", region_name=settings.s3_region).put_object(
            Bucket=settings.s3_bucket, Key=key, Body=data
        )
    else:
        path = Path(settings.storage_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return key


def read_file(key: str) -> bytes:
    settings = get_settings()
    if settings.storage_backend == "s3":
        import boto3

        obj = boto3.client("s3", region_name=settings.s3_region).get_object(
            Bucket=settings.s3_bucket, Key=key
        )
        return obj["Body"].read()
    return (Path(settings.storage_dir) / key).read_bytes()
