# -*- coding: utf-8 -*-
"""单据流转。全部落在 approval schema。

三张表：

    flow_rule     什么单据、多少钱，走哪几道 —— 规则即数据，律所自己配
    ticket        一张单子
    ticket_step   这张单子走过/要走的每一道

★ **这个模块的全部难点在一句话上：财务只看得到单据和金额，看不到案情。**

  律师提一张请款单，财务要付这笔钱。财务需要知道的是：多少钱、付给谁、
  什么名目、谁批的。财务**不需要**知道这是哪个当事人、什么纠纷、
  案情如何 —— 而律所里最敏感的东西恰恰是这些。

  所以隔离不是靠权限，是靠**结构**：ticket 上根本没有案情字段。
  它只有 `subject`（事由，一句话）和金额。想看案情，去案件页看，
  那受 case.view 管，是另一回事。

  ★ 结构隔离之后，剩下唯一的泄漏口是 `subject` 那一栏 ——
    律师顺手写成「宏远诉凯达工程款纠纷的鉴定费」，案情就跟着单子走了。
    所以 service 里有一道检查：事由里出现案件名称就拦下来。
    **不是不信任律师，是这一栏太顺手了。**

★ 为什么不自动从案件带出事由：那正是最方便、也最会泄漏的做法。
  省下的两秒钟，代价是每张单子都带着案由标题在所里流转。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "approval"


def _table_args(*extra):
    return tuple(extra) + ({"schema": SCHEMA},)


#: 单据类型
TICKET_KINDS = ("payment", "reimburse", "seal", "disburse", "other")

TICKET_KIND_LABELS = {
    "payment": "请款（垫付）",
    "reimburse": "报销",
    "seal": "用印",
    "disburse": "付款给第三方",
    "other": "其他",
}

#: 单据状态。
#:
#: ★ `paid` / `done` 之后不许改。一张已经付过钱的单子被人改了金额，
#:   账就再也对不上了 —— 要改只能作废重提。
TICKET_STATUSES = ("draft", "pending", "approved", "rejected", "paid",
                   "done", "cancelled")

TICKET_STATUS_LABELS = {
    "draft": "草稿", "pending": "审批中", "approved": "已批待付",
    "rejected": "已驳回", "paid": "已付款", "done": "已完成",
    "cancelled": "已作废",
}

LOCKED_TICKET_STATUSES = ("paid", "done", "cancelled")

STEP_STATUSES = ("waiting", "approved", "rejected", "skipped")

#: 印章种类。★ 用印是律所的重管控事项 —— 盖错章的后果比写错文书重得多
SEAL_KINDS = ("firm", "contract", "legal_person", "finance", "other")

SEAL_KIND_LABELS = {
    "firm": "公章", "contract": "合同专用章", "legal_person": "法定代表人章",
    "finance": "财务专用章", "other": "其他",
}


class FlowRule(Base):
    """什么单据、多少钱，走哪几道。

    ★ 和时限规则、费率一样：**规则是数据不是代码**。
      「五千以下团队负责人批就行，五千以上要过管委会」是所里定的门槛，
      各所不同，而且会改。写死在代码里，改一次门槛要发一次版。
    """

    __tablename__ = "flow_rule"
    __table_args__ = _table_args(
        Index("ix_flow_rule_kind", "tenant_id", "kind", "min_amount"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: 金额区间（含下界，不含上界）。上界为空表示不封顶
    min_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    max_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(18, 2), nullable=True
    )

    #: 流转链，形如 "团队负责人:approval.approve_team|管委会:approval.approve_firm"
    #:
    #: ★ 存成字符串而不是 JSON：这东西要给人看、要给人改，
    #:   一行字符串在界面上、在日志里、在报错信息里都是可读的。
    #:   JSON 到了报错信息里就是一串转义符。
    steps: Mapped[str] = mapped_column(Text, nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Ticket(Base):
    """一张单子。

    ★ **这里没有案情字段，是刻意的。** 见模块头。
    """

    __tablename__ = "ticket"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "number", name="uq_ticket_number"),
        Index("ix_ticket_status", "tenant_id", "status"),
        Index("ix_ticket_requester", "tenant_id", "requested_by"),
        Index("ix_ticket_case", "tenant_id", "case_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 单据号，全所唯一
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    #: 关联案件。★ **只用于回写账务和统计**，不是给财务看案情用的。
    #:   describe() 对看不见这个案子的人会把它抹掉，并明说抹掉了。
    case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    #: ---- 财务看得到的那几栏，仅此而已 ----
    #:
    #: 事由：一句话说清这笔钱是干什么的。
    #: ★ **不要写案件名称、当事人、案由** —— 这一栏会跟着单子在所里流转，
    #:   service 里有检查会拦。
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)
    #: 收款方
    payee: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    #: 收款账号。★ 敏感 —— describe() 只对有付款权限的人显示完整号码，
    #:   其余人看到的是掩码。生产环境应当加密存储（当前未加密，见文档）
    payee_account: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 期望付款日
    need_by: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    #: 申请人自己的补充说明。同样受事由那道检查约束
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: 附件（doc 模块的 document id，逗号分隔）—— 发票、收据、法院缴费通知
    doc_ids: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    #: ---- 用印专用 ----
    seal_kind: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    #: 要盖章的文件名
    seal_document: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 申请份数
    seal_copies: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: ★ **实际盖了几份**。申请 3 份盖了 5 份是重大问题 ——
    #:   所以用印执行时必须回填这个数，不是走完流程就算完
    seal_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: 是否外带（原件带出所）
    seal_takeaway: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    #: 当前走到第几道（对应 ticket_step.seq）
    current_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    requested_by: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 付款/执行
    paid_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    pay_reference: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    #: 作废/驳回原因
    close_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    #: 回写到 billing 的代垫费用 id
    disbursement_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )


class TicketStep(Base):
    """单子走过/要走的一道。

    ★ 整条链在**提交时就固化下来**，不是每次查询现算。
      规则后来改了（门槛从五千提到一万），已经在走的单子不该跟着变 ——
      否则一张走到一半的单子会突然多出一道审批，或者少掉一道，
      而没人说得清它到底该几道。
    """

    __tablename__ = "ticket_step"
    __table_args__ = _table_args(
        UniqueConstraint("ticket_id", "seq", name="uq_step_seq"),
        Index("ix_step_ticket", "ticket_id"),
        Index("ix_step_pending", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_id: Mapped[int] = mapped_column(Integer, nullable=False)

    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 走这一道需要什么权限
    required_perm: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(12), default="waiting", nullable=False)
    decided_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: ★ 驳回必须写理由 —— 不说为什么，对方只会原样再提一次
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)
