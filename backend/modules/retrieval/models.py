"""检索记录。全部落在 retrieval schema。

**为什么要留档**：

  1. Step 13 的引用留痕要靠 `trace_id` 把「文书里的这句话」串回
     「当时那次检索」。没有这条记录，引用就是一段悬空的文字。

  2. 花费要按租户记账 —— 将来做配额、做成本分摊都要它。

  3. 律师要能看「我上次在这个案子里查过什么」。

**为什么存引用而不存原文**：检索库会重建（换数据源、重新切分、扩充法条），
原文可能变。所以存的是**稳定标识 + 当时的片段快照**：

    source_id  法释〔2020〕25号#第十四条   ← 库重建后仍能定位
    snippet    当时看到的原文片段          ← 用于"当时的依据是什么"
    corpus_version  919d5194c-ae1a9b07    ← 这条引用出自哪个版本的库
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "retrieval"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


#: search 纯检索（0 成本）/ answer 问答 / extract 要素抽取 / compare 类案对比
QUERY_KINDS = ("search", "answer", "extract", "compare")

#: 任务状态。纯检索是同步的，直接 done
QUERY_STATUSES = ("queued", "running", "done", "failed")


class SearchLog(Base):
    """一次检索。"""

    __tablename__ = "search_log"
    __table_args__ = _table_args(
        Index("ix_search_case", "case_id"),
        Index("ix_search_actor", "actor_id"),
        Index("ix_search_tenant_time", "tenant_id", "id"),
        Index("ix_search_job", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 挂在哪个案件下。为空 = 不针对具体案件的独立检索
    case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actor_id: Mapped[int] = mapped_column(Integer, nullable=False)

    kind: Mapped[str] = mapped_column(String(16), default="search", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="done", nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    #: 检索服务改写出的检索式（JSON 数组），让律师看懂"它到底查了什么"
    queries: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    intent: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 引用校验告警：编造条号、查无此案、已废止…
    warnings: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    #: 检索服务侧的任务 id（长任务轮询用）
    job_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: ★ 引用留痕的锚点，Step 13 靠它把文书串回这次检索
    trace_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 语料版本指纹。库重建后能区分"这条引用出自哪个版本"
    corpus_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 人民币，按租户记账
    cost_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal("0"), nullable=False
    )
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SearchCitation(Base):
    """这次检索命中的引用。

    **存稳定标识 + 片段快照，不存原文全文** —— 见模块 docstring。
    """

    __tablename__ = "search_citation"
    __table_args__ = _table_args(
        Index("ix_citation_log", "log_id"),
        Index("ix_citation_source", "tenant_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    log_id: Mapped[int] = mapped_column(
        ForeignKey("{0}.search_log.id".format(SCHEMA), ondelete="CASCADE"),
        nullable=False,
    )
    #: 在答复里的序号（S1、S2…）
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    #: ★ 稳定标识，不是 chunk_id
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    #: 当时看到的原文片段
    snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    #: 当时的 chunk_id 等，仅供加速，不作身份
    locator: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    effective_status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 答复是否真的引用了它
    used: Mapped[bool] = mapped_column(
        __import__("sqlalchemy").Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
