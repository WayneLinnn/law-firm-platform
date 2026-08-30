# -*- coding: utf-8 -*-
"""引用留痕。全部落在 citation schema。

**两张表：一条依据是什么，和它在文书里出现在哪。**

    citation   依据本身的**快照** —— 半年后检索库重建了，这份文书里的
               【S3】还得能说清当时引的是什么、原文长什么样、当时是否有效
    anchor     每一次出现的**位置** —— 精确到字符区间和所在句子

★ 为什么快照要存全（而不是只存一个 source_id）：

  Step 8 就定过，检索库**会重建**（换数据源、重新切分、扩充法条）。
  重建之后 chunk_id 全变，条文内容也可能改版。如果这里只存一个 id，
  半年后律师点开文书里的【S3】，看到的可能是**另一条内容**，
  甚至什么都看不到 —— 而这份文书已经递出去了。

  所以存下当时的：原文片段、时效状态、语料版本指纹、检索的 trace_id。
  库怎么变，这份文书的引用都还原得回来。

★ 为什么用 (doc_kind, doc_id) 而不是外键指向 draft：

  铁律是每个模块独占一个 schema。跨 schema 建外键会把两个模块焊死，
  删 draft 模块时这里会连带崩掉。用一对不透明的标识，
  谁产出的文书都能挂进来 —— 将来 workflow 的意见书也走这套。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
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

SCHEMA = "citation"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


#: 引用挂在什么东西上。现在只有文书，将来流程产出的意见书也走这里
DOC_KINDS = ("draft", "workflow")


class Citation(Base):
    """一条依据在某份文书里的快照。ordinal 就是文里的【S数字】。"""

    __tablename__ = "citation"
    __table_args__ = _table_args(
        UniqueConstraint("doc_kind", "doc_id", "ordinal", name="uq_citation_ordinal"),
        Index("ix_cite_doc", "doc_kind", "doc_id"),
        Index("ix_cite_source", "source_id"),
        Index("ix_cite_trace", "trace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 文里的编号：【S3】 → 3
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    #: statute 法条 / case 判例
    source_type: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    #: ★ 稳定标识，**不是 chunk_id**。库重建后仍能定位到同一条内容
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 当时看到的原文片段 —— 库改版了也还原得出「当时依据的是这段话」
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 当时的定位（chunk_id 等），仅供加速，不作身份
    locator: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 当时的时效状态：现行有效 / 已失效 / 未标注
    effective_status: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    #: 语料版本指纹。库重建后能区分「这条引用出自哪个版本」
    corpus_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 关联到当时那次检索（Step 8 埋的）。顺着它能查回检索记录
    trace_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 这条是为哪个争议焦点/检索式取回来的
    issue: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Anchor(Base):
    """一次出现：这条依据被写在文书的哪个位置。

    ★ **锚点是推导出来的，不是手工维护的。**

    位置来源就是正文里的【S3】这个标记本身。每次正文变了就整段重算 ——
    所以偏移量永远不会漂。手工维护偏移量的方案在第一次编辑后就废了。
    """

    __tablename__ = "anchor"
    __table_args__ = _table_args(
        Index("ix_anchor_doc", "doc_kind", "doc_id", "part_key"),
        Index("ix_anchor_citation", "citation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    citation_id: Mapped[int] = mapped_column(
        ForeignKey("citation.citation.id", ondelete="CASCADE"), nullable=False
    )
    doc_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 文书的哪一节
    part_key: Mapped[str] = mapped_column(String(64), nullable=False)

    #: 标记本身在这一节正文里的字符区间
    start: Mapped[int] = mapped_column(Integer, nullable=False)
    end: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 标记所在的那句话 —— 「点任一句看出处」靠它
    sentence_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sentence_start: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sentence_end: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sentence_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
