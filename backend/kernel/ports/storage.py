"""对象存储契约 —— 案卷文件。实现见 adapters/storage/（MinIO / OSS）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@dataclass
class StoredFile:
    key: str
    size: int
    content_type: str
    etag: str = ""


@runtime_checkable
class StoragePort(Protocol):
    def put(self, key: str, stream: BinaryIO, content_type: str = "") -> StoredFile:
        ...

    def get(self, key: str) -> BinaryIO:
        ...

    def delete(self, key: str) -> None:
        ...

    def presigned_url(self, key: str, expires_seconds: int = 600) -> str:
        ...
