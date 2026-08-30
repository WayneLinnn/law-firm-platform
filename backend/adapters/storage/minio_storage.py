"""MinIO 适配器 —— StoragePort 的实现。

只有 kernel/bootstrap.py 可以 import 本文件。

MinIO 兼容 S3 协议，将来换阿里云 OSS / 腾讯云 COS 只需换这个适配器，
业务模块一行不改。
"""
from __future__ import annotations

import io
import logging
from datetime import timedelta
from typing import BinaryIO

from kernel.config import Settings
from kernel.ports.storage import StoredFile

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"


class MinioStorage:
    """实现 kernel.ports.StoragePort。"""

    def __init__(self, settings: Settings) -> None:
        from minio import Minio

        self._bucket = settings.minio_bucket
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info("已创建 bucket %s", self._bucket)

    # ------------------------------------------------------------------ 接口

    def put(self, key: str, stream: BinaryIO, content_type: str = "") -> StoredFile:
        data = stream.read() if hasattr(stream, "read") else bytes(stream)
        ctype = content_type or DEFAULT_CONTENT_TYPE
        result = self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data), content_type=ctype
        )
        return StoredFile(
            key=key, size=len(data), content_type=ctype, etag=result.etag or ""
        )

    def get(self, key: str) -> BinaryIO:
        resp = self._client.get_object(self._bucket, key)
        try:
            return io.BytesIO(resp.read())
        finally:
            resp.close()
            resp.release_conn()

    def delete(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def presigned_url(self, key: str, expires_seconds: int = 600) -> str:
        return self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(seconds=expires_seconds)
        )


def build_storage_adapter(settings: Settings):
    """给 bootstrap 用的工厂。backend=fake 时用内存实现。"""
    if settings.storage_backend == "fake":
        from adapters.storage.memory import MemoryStorage

        return MemoryStorage()
    return MinioStorage(settings)
