"""流程运行记录。全部落在 workflow schema。

**这张表和 LangGraph 的 checkpoint 是两回事，别混。**

    workflow.workflow_run       ← 本文件。业务视角：谁、什么案子、跑到哪、在等谁
    workflow.checkpoints / ...  ← LangGraph 自己建的私有表，见 adapters/workflow

前者是我们要查询、要授权、要展示的业务记录；后者是编排引擎的内部存档，
结构由库决定，我们不碰、也不写迁移。两者用 `run_id`（= thread_id）串起来。

**为什么必须落库而不是放内存**：一个建设工程案子的流程可能跑两个月，
中间要换人接力、要等鉴定结果。内存版一重启就全丢了。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "workflow"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


#: running        正在跑
#: waiting_human  停在人工确认点，等律师回答（★ HITL 只在这一层，见 C1）
#: done           跑完
#: failed         节点抛错，state 里留了错误信息
#: cancelled      人工中止。只改业务记录，checkpoint 留着供复盘
RUN_STATUSES = ("running", "waiting_human", "done", "failed", "cancelled")


class WorkflowRun(Base):
    """一次流程运行。

    `run_id` 同时是 LangGraph 的 thread_id —— 两边靠它对齐。
    用 uuid 而不是自增主键：它要写进 checkpoint、要出现在 URL 里，
    自增 id 会泄露"本所一共跑了多少流程"。
    """

    __tablename__ = "workflow_run"
    __table_args__ = _table_args(
        Index("ix_run_case", "case_id"),
        Index("ix_run_tenant_status", "tenant_id", "status"),
        Index("ix_run_actor", "started_by"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: = LangGraph thread_id
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 挂在哪个案件下。流程一定属于某个案件 —— 没有案件的流程没有意义
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    #: 当前停在哪个节点。给律师看"走到哪一步了"
    current_node: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 停在人工确认点时，要问律师的问题
    pending_question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 失败时的错误信息
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    started_by: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkflowStep(Base):
    """节点流转留痕。

    ★ 为什么不靠 LangGraph 的 checkpoint history 来看流转：
      checkpoint 是引擎的私有结构，格式随版本变；而"这个案子的流程
      什么时候走到哪一步、谁点的确认"是**要给合规看的业务事实**，
      必须由我们自己按稳定结构记下来。
    """

    __tablename__ = "workflow_step"
    __table_args__ = _table_args(
        Index("ix_step_run", "run_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    #: entered 进入节点 / asked 抛出人工确认 / answered 律师回答 / failed 出错
    event: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 人工确认时，律师实际回答了什么 —— 事后追责要看这个
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
