from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path

from minio import Minio

from app.config import Settings


class ObjectStore:
    def __init__(self, settings: Settings):
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
        self.public_client = Minio(
            settings.minio_public_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_public_secure,
            region=settings.minio_region,
        )

    def download(self, bucket: str, object_key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.fget_object(bucket, object_key, str(destination))
        return destination

    def upload_file(
        self,
        bucket: str,
        object_key: str,
        source: Path,
        content_type: str,
    ) -> None:
        self.client.fput_object(
            bucket,
            object_key,
            str(source),
            content_type=content_type,
        )

    def upload_bytes(
        self,
        bucket: str,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> None:
        self.client.put_object(
            bucket,
            object_key,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )

    def presigned_get_url(
        self,
        bucket: str,
        object_key: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        return self.public_client.presigned_get_object(bucket, object_key, expires=expires)
