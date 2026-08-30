# -*- coding: utf-8 -*-
"""待办与时限。全部落在 task schema。

三张表，回答三个不同的问题：

    deadline_rule   **法律规定**多久 —— 律所自己能加，是数据不是代码
    deadline        **这个案子**的某条期限什么时候届满
    task            **谁**在什么时候要做什么

★ 为什么时限和任务分开：
  任务是人安排的，可以改期、可以取消。时限是法律定的，**改不了** ——
  只能记录"我们何时行使了权利"或"已经过期了"。
  混成一张表，早晚会出现有人把一条法定期限"往后拖三天"。

★ 为什么规则要做成表（手册 Step 15 的硬要求）：
  时限规则有地方差异、有新司法解释、有律所自己的内部提前量。
  写死在代码里，每加一条都要发版。做成表，律所自己维护。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "task"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


#: ok       还早
#: urgent   30 天内届满
#: expired  已过期
#: unknown  ★ 起算日期缺失，**算不出来** —— 必须明说，不能静默跳过
#: done     已经行使/已完成（比如已起诉、已发函）
#: waived   律师确认不适用本案
DEADLINE_STATUSES = (
    "ok", "urgent", "expired",
    "unknown",    # 缺起算日，**补齐就能算** —— 要催
    "no_fixed",   # 规则本来就没有固定期间 —— 不催，但也不消失
    "done", "waived",
)

TASK_STATUSES = ("todo", "doing", "done", "cancelled")
PRIORITIES = ("high", "normal", "low")


class DeadlineRule(Base):
    """时限规则。**是数据，不是代码。**"""

    __tablename__ = "deadline_rule"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "code", name="uq_rule_code"),
        Index("ix_rule_cause", "tenant_id", "cause_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: 法律依据。★ 必填 —— 一条算不出来路的期限，律师不敢信也不该信
    basis: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    #: 哪个案由适用。空 = 所有案由
    cause_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 我方是什么角色才适用。空 = 都适用
    role: Mapped[str] = mapped_column(String(24), default="", nullable=False)

    #: 从哪个日期起算（要素字段名，如 payable_on / completed_on / served_on）
    anchor_field: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 期间长度。两个都为 0 = 没有固定期间（比如"合理使用年限内"），只做提示
    months: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: True = 除斥期间，过期即丧失实体权利，**不中断不中止**
    fatal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 能不能中断/中止（诉讼时效能，除斥期间不能）
    suspendable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: 最长保护期（年）。0 = 不适用。
    #:
    #: ★ 这是整个引擎最容易漏、漏了后果最重的一条。《民法典》188 条 2 款：
    #:   诉讼时效自权利受损害之日起超过 20 年的，法院不予保护，
    #:   而且**这 20 年不适用中断、中止**。
    #:   如果不管它，一个律师每年发一次催款函、系统每年老老实实把届满日
    #:   往后推三年，界面会一直显示「正常」—— 一直显示到第 21 年。
    #:   那正是这个模块最该防的东西：**一个笃定的绿色，底下什么都没有。**
    outer_years: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 提前多少天开始预警
    warn_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    #: ★ 规则自己知道它盖不住哪些情形，写在这里，生成的时限上会带出去。
    #:   覆盖不了的**必须标出来让人确认**，不能默默算一个数字。
    caveat: Mapped[str] = mapped_column(Text, default="", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: 内置规则（我们随平台带的）vs 律所自己加的
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Deadline(Base):
    """一个案子上的一条时限。"""

    __tablename__ = "deadline"
    __table_args__ = _table_args(
        UniqueConstraint("case_id", "rule_code", name="uq_case_rule"),
        Index("ix_deadline_case", "case_id"),
        Index("ix_deadline_due", "tenant_id", "status", "due_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    basis: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    anchor_field: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 起算日。为空 = 算不出来，status 会是 unknown
    anchor_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    fatal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    warn_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    caveat: Mapped[str] = mapped_column(Text, default="", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: 中断：重新起算（诉讼时效催收之后）。除斥期间不允许
    interrupted_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    interrupt_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    #: 中止事由**消除**之日（不是发生之日）。
    #:
    #: ★ 中止和中断是两回事，界面上必须分开，因为算法完全不同：
    #:     中断（195 条）—— 从中断之日**重新起算**整个期间
    #:     中止（194 条）—— 从事由**消除**之日起满 **6 个月**届满
    #:   记的是「消除日」而不是「发生日」，因为 194 条的算式只用得上消除日；
    #:   存发生日会让人以为系统在算中间那段，其实没有。
    suspend_end_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    suspend_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    #: 最长保护期届满日（算出来存下来，便于排序和展示）
    outer_due_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    #: ★ 律师确认过了没有。**没确认的时限只能算提示，不能算数**
    confirmed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: rule 自动生成 / manual 手工加的
    source: Mapped[str] = mapped_column(String(16), default="rule", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )


class Task(Base):
    """一件要做的事。人安排的，可以改期、可以取消。"""

    __tablename__ = "task"
    __table_args__ = _table_args(
        Index("ix_task_case", "case_id"),
        Index("ix_task_assignee", "tenant_id", "assignee_id", "status"),
        Index("ix_task_due", "tenant_id", "status", "due_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 挂在哪个案件下。为空 = 不针对具体案件的事务性任务
    case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)

    assignee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    due_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String(8), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="todo", nullable=False)

    #: 由哪条时限派生出来的（"优先受偿权还剩 20 天 → 建个任务去起诉"）
    deadline_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    done_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    done_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
