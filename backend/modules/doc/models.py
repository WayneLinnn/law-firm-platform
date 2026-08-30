# -*- coding: utf-8 -*-
"""案卷文档。全部落在 doc schema。

**两张表，分工是刻意的：**

    document          一份文档的**身份**：属于哪个案子、放在哪个卷宗、叫什么
    document_version  这份文档的**每一版**：文件本体在对象存储里的键、大小、指纹

为什么要拆开：律所的文书是反复改的。「起诉状」这个东西从头到尾是同一份文档，
但它有 v1、v2、v3。**版本行只增不改** —— 改了旧版本，
就没法回答「我们当时递上去的是哪一版」，而这个问题在纠纷里是要命的。

文件本体不进数据库，进对象存储（MinIO，走 StoragePort）。
库里只留元数据 + 抽出来的文本。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "doc"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


#: 卷宗分类。律所整理案卷本来就是按这几摞分的。
#: 不做成表：它是**默认值**不是字典 —— 律所想写别的字符串也随他，
#: 提前上一张字典表只会多一个要维护的东西。
DEFAULT_FOLDERS = (
    "诉讼文书",
    "证据材料",
    "合同协议",
    "往来函件",
    "法律意见",
    "其他",
)

#: 文本抽取的结果。★ 必须分得这么细，见 extract.py 里的说明。
TEXT_STATUSES = (
    "ok",           # 抽出正文了，全文检索能搜到
    "empty",        # 文件本身没有可提取的文字（**扫描件就是这种**）
    "unsupported",  # 这个格式我们不解析（图片、压缩包…）
    "failed",       # 解析出错（加密 PDF、文件损坏）
)


class Document(Base):
    """一份文档。"""

    __tablename__ = "document"
    __table_args__ = _table_args(
        Index("ix_doc_case", "case_id"),
        Index("ix_doc_tenant_folder", "tenant_id", "folder"),
        Index("ix_doc_owner", "created_by"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 挂在哪个案件下。**案卷必须属于案件** —— 不挂案件的文件没法判权限，
    #: 也就没法保证「看不到这个案子的人看不到它的材料」。
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    folder: Mapped[str] = mapped_column(String(64), default="其他", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: 当前版本号。取文件时默认取它
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 软删。★ 案卷材料**不做物理删除** —— 谁在什么时候删了什么，
    #: 是合规要问的问题；真删了就答不上来。
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )


class DocumentVersion(Base):
    """一个版本。**只增不改。**"""

    __tablename__ = "document_version"
    __table_args__ = _table_args(
        UniqueConstraint("document_id", "version", name="uq_doc_version"),
        Index("ix_ver_doc", "document_id", "version"),
        Index("ix_ver_sha", "sha256"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("doc.document.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 对象存储里的键。刻意只用 ASCII：文件名里的中文、空格、括号
    #: 在不同存储后端（MinIO / OSS / S3）上的转义规则不一样，踩过就回不来了。
    #: 真实文件名存在 filename 字段里。
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    #: 内容指纹。用来判断「这次上传的和上一版是不是同一个文件」，
    #: 也是将来做完整性校验的锚点。
    sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    #: 抽出来的正文，供全文检索。抽不出来就是空 —— 但**状态要说清楚**
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    text_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    uploaded_by: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
