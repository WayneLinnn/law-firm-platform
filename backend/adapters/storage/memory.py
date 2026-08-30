"""内存存储 —— 无 MinIO 时的本地开发与 CI 用。

设 LFP_STORAGE_BACKEND=fake 即可启用。
"""
from __future__ import annotations

import hashlib
import io
from typing import BinaryIO, Dict, Tuple

from kernel.errors import NotFound
from kernel.ports.storage import StoredFile


class MemoryStorage:
    """实现 kernel.ports.StoragePort。"""

    def __init__(self) -> None:
        self._data: Dict[str, Tuple[bytes, str]] = {}

    def put(self, key: str, stream: BinaryIO, content_type: str = "") -> StoredFile:
        data = stream.read() if hasattr(stream, "read") else bytes(stream)
        ctype = content_type or "application/octet-stream"
        self._data[key] = (data, ctype)
        return StoredFile(
            key=key, size=len(data), content_type=ctype,
            etag=hashlib.md5(data).hexdigest(),
        )

    def get(self, key: str) -> BinaryIO:
        if key not in self._data:
            raise NotFound("对象 {0} 不存在".format(key))
        return io.BytesIO(self._data[key][0])

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def presigned_url(self, key: str, expires_seconds: int = 600) -> str:
        return "memory://{0}?expires={1}".format(key, expires_seconds)
