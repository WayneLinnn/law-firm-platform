"""存储适配器。

内存实现无条件跑；MinIO 实现连真实容器跑，连不上则 skip
（这样 CI 里没有 MinIO 也不会红）。
"""
import io
import uuid

import pytest

from kernel.config import Settings
from kernel.errors import NotFound
from kernel.ports import StoragePort


# ---------------------------------------------------------------- 内存实现

def test_memory_roundtrip():
    from adapters.storage.memory import MemoryStorage

    s = MemoryStorage()
    meta = s.put("a/b.txt", io.BytesIO("案卷内容".encode("utf-8")), "text/plain")
    assert meta.key == "a/b.txt"
    assert meta.size == len("案卷内容".encode("utf-8"))
    assert s.get("a/b.txt").read().decode("utf-8") == "案卷内容"
    s.delete("a/b.txt")
    with pytest.raises(NotFound):
        s.get("a/b.txt")


def test_memory_satisfies_the_port():
    from adapters.storage.minio_storage import build_storage_adapter

    assert isinstance(build_storage_adapter(Settings(storage_backend="fake")), StoragePort)


# ---------------------------------------------------------------- 真实 MinIO

@pytest.fixture
def minio_storage():
    from adapters.storage.minio_storage import MinioStorage

    settings = Settings(minio_bucket="lfp-test")
    try:
        return MinioStorage(settings)
    except Exception as exc:  # 容器没起就跳过，不让 CI 变红
        pytest.skip("MinIO 不可用：{0}".format(exc))


def test_minio_roundtrip(minio_storage):
    key = "test/{0}.txt".format(uuid.uuid4().hex)
    payload = "起诉状正文".encode("utf-8")

    meta = minio_storage.put(key, io.BytesIO(payload), "text/plain")
    assert meta.size == len(payload)
    assert meta.etag

    assert minio_storage.get(key).read() == payload

    url = minio_storage.presigned_url(key, expires_seconds=60)
    assert url.startswith("http") and key in url

    minio_storage.delete(key)


def test_minio_satisfies_the_port(minio_storage):
    assert isinstance(minio_storage, StoragePort)
