"""doc 模块的对外唯一出口。

★ `save_text` 是给 workflow 用的：Step 10 的法律意见书跑完就该落进案卷。
  workflow 通过**可选依赖**调它 —— 没装 doc 模块，流程照常跑，只是不归档。
"""
from modules.doc.service import (
    archive_inventory,
    archive_blockers,
    download,
    folders,
    get_document,
    get_version,
    list_documents,
    rename,
    restore,
    save_text,
    search,
    soft_delete,
    stats,
    unsearchable,
    upload,
    versions_of,
)

__all__ = [
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory", "archive_blockers",
    "upload", "save_text", "download",
    "get_document", "get_version", "versions_of", "list_documents",
    "search", "unsearchable", "stats",
    "rename", "soft_delete", "restore", "folders",
]
