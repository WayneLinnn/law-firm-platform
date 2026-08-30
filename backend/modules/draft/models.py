# -*- coding: utf-8 -*-
"""文书草稿。全部落在 draft schema。

**按「节」存，不按整篇存。** 理由很实际：

律师改完「事实与理由」之后，想重新生成一遍「诉讼请求」——
如果整篇是一个字段，重新生成就把他改过的东西冲掉了。
按节存，就能只重跑一节，别的原样不动。

这也是为什么每节要记 `source`（这段是填出来的、模型写的、还是人写的）
和 `edited`（律师动过没有）：**律师亲手改过的段落，重新生成时要拦一下。**
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
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

SCHEMA = "draft"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


#: draft   在改
#: final   律师定稿（定稿即归档进案卷）
#: void    作废
DRAFT_STATUSES = ("draft", "final", "void")


class Draft(Base):
    """一份文书草稿。"""

    __tablename__ = "draft"
    __table_args__ = _table_args(
        Index("ix_draft_case", "case_id"),
        Index("ix_draft_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)

    #: 依据从哪来的。挂了流程就用流程检索到的，否则用本模块自己检索的
    source_run_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 可引用的依据快照（JSON）。★ 存下来是因为**检索库会重建** ——
    #: 半年后回看这份文书，得知道当时手里有的是哪些材料
    citations: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    #: 定稿后归档到案卷的文档 id
    document_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    finalized_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    finalized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cost_cny: Mapped[str] = mapped_column(String(24), default="0", nullable=False)

    #: 定稿那一刻的引用体检结论。
    #:
    #: ★ 存下来是为了**事后能证明定稿时提示过**。法条后来又变了不要紧 ——
    #:   要紧的是这份文书递出去的时候，律师被告知过什么。
    #:   和 billing 的 legality_note 一个道理。
    recheck_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )


class DraftSection(Base):
    """文书的一节。"""

    __tablename__ = "draft_section"
    __table_args__ = _table_args(
        UniqueConstraint("draft_id", "key", name="uq_draft_section"),
        Index("ix_section_draft", "draft_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("draft.draft.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    #: fill 自动填充 / generate 模型起草 / manual 律师手写
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 这段实际是怎么来的：fill / model / manual / empty
    source: Mapped[str] = mapped_column(String(16), default="empty", nullable=False)
    #: ★ 律师亲手改过 —— 重新生成时要先问一句
    edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 这一节用到的依据编号（JSON 数组），Step 13 会把它规范成独立的引用表
    used: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    #: 引用校验的告警：引了不存在的编号、该给依据却没给…
    warnings: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    #: 填充类缺数据时说明缺什么
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    updated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
