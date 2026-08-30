"""案件与事项。全部落在 case schema。

结构：

    案由字典 CauseOfAction   建设工程施工合同纠纷 / 劳动争议 …（树形，可扩展）
        │  决定用哪张流程图（Step 9-10）
        ▼
    案件 Case                一个委托单元，对应一份委托合同、一个收费单元
        ├── 事项 Matter      程序阶段：一审 / 二审 / 执行；非诉的工作包
        ├── 当事人 CaseParty  我方 / 对方 / 第三人 —— 指向 crm 主体库
        └── 承办 CaseMember   主办律师 + 协办

两个设计是刻意的：

  1. **当事人独立成表且指向 crm.Party** —— 这是利冲的另一半数据源。
     Step 7 要回答"这个主体在别的案子里是不是对方"，如果对方名称只是
     案件表上的一个字符串，这个查询做不了。

  2. **状态机显式声明** —— 案件状态乱跳是最容易出的数据问题（已结案的
     案子又冒出新账单、利冲还没过就开始办案）。合法流转写死在
     ALLOWED_TRANSITIONS 里，非法流转直接报错。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
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

SCHEMA = "case"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


def _fk(table: str) -> str:
    """外键目标。**不要手工加引号** —— SQLAlchemy 按未加引号的 key 在
    metadata 里查表，引号由方言在生成 DDL 时自动补。

    ★ 注意：`case` 是 SQL 保留字。ORM 与 Core 会自动加引号，
      但**任何手写 SQL 都必须写成 "case"."case"**，否则语法错误。
    """
    return "{0}.{1}.id".format(SCHEMA, table)


# ---------------------------------------------------------------- 案由字典


#: 业务大类。决定默认流程模板与收费口径。
CASE_CATEGORIES = ("civil", "criminal", "admin", "arbitration", "nonlit")


class CauseOfAction(Base):
    """案由。树形（一级 → 二级），律所可自行增补。

    `workflow_template` 是 Step 9-10 选流程图的依据 —— 建案时按案由自动
    套上对应的办案流程。留空表示还没有对应流程，走通用流程。
    """

    __tablename__ = "cause_of_action"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "code", name="uq_cause_tenant_code"),
        Index("ix_cause_parent", "parent_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(_fk("cause_of_action"), ondelete="RESTRICT"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(16), default="civil", nullable=False)
    #: 对应的办案流程模板 key（Step 9-10）
    workflow_template: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 案件


#: 案件状态。**流转规则见 service.ALLOWED_TRANSITIONS，不是随便改的字段。**
CASE_STATUSES = (
    "draft",           # 草稿：刚录入，还没做利冲
    "conflict_check",  # 利冲审查中（Step 7 卡在这里）
    "pending",         # 利冲通过，待立案/待签约
    "active",          # 办理中
    "closing",         # 结案中（收尾、归档准备）
    "closed",          # 已结案
    "archived",        # 已归档
    "terminated",      # 终止（撤案、解除委托）
)


class Case(Base):
    """一个委托单元。对应一份委托代理合同、一个收费单元。

    lead_id（主办律师）与 org_unit_id（承办团队）是数据范围过滤的依据。
    """

    __tablename__ = "case"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "code", name="uq_case_tenant_code"),
        Index("ix_case_lead", "lead_id"),
        Index("ix_case_org", "org_unit_id"),
        Index("ix_case_client", "client_id"),
        Index("ix_case_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 所内案件编号
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    cause_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(_fk("cause_of_action"), ondelete="RESTRICT"), nullable=True
    )
    #: 委托人（crm.client.id）。允许为空 —— 利冲未过之前还没正式建客户
    client_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)

    #: 主办律师（identity.user.id）—— SELF 数据范围看这个
    lead_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 承办团队（identity.org_unit.id）—— DEPT / DEPT_TREE 范围看这个
    org_unit_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    court: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    #: 法院/仲裁机构的案号，与所内编号不是一回事
    external_no: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    opened_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    closed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseStatusLog(Base):
    """状态流转留痕。结案、终止这类动作要能追溯是谁在什么时候做的。"""

    __tablename__ = "case_status_log"
    __table_args__ = _table_args(Index("ix_status_log_case", "case_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("case"), ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 当事人


#: 诉讼地位。`ours` 标记哪一方是我们代理的。
PARTY_ROLES = (
    "plaintiff",    # 原告 / 申请人
    "defendant",    # 被告 / 被申请人
    "third_party",  # 第三人
    "appellant",    # 上诉人
    "appellee",     # 被上诉人
    "other",
)


class CaseParty(Base):
    """案件当事人。**指向 crm 主体库，不是一个名称字符串。**

    Step 7 的利冲要问："这个主体在所里别的案子里，是不是站在对面？"
    只有当事人独立成表并指向统一主体，这个查询才做得了。
    """

    __tablename__ = "case_party"
    __table_args__ = _table_args(
        UniqueConstraint("case_id", "party_id", "role", name="uq_case_party"),
        Index("ix_case_party_party", "party_id"),
        Index("ix_case_party_case", "case_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("case"), ondelete="CASCADE"), nullable=False
    )
    #: crm.party.id
    party_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    #: 是不是我方当事人（我们代理的那一方）
    is_ours: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 承办人


#: 在案件中的分工
CASE_MEMBER_ROLES = ("lead", "member", "assistant")


class CaseMember(Base):
    """案件承办人。主办 + 协办 + 助理。

    与 identity 的团队归属不是一回事：团队是常设编制，案件承办是按案指派，
    可以跨团队借人。
    """

    __tablename__ = "case_member"
    __table_args__ = _table_args(
        UniqueConstraint("case_id", "user_id", name="uq_case_member"),
        Index("ix_case_member_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("case"), ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    joined_on: Mapped[date] = mapped_column(Date, nullable=False)
    left_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 事项


#: 程序阶段 / 非诉工作包
MATTER_KINDS = (
    "first_instance",   # 一审
    "second_instance",  # 二审
    "retrial",          # 再审
    "enforcement",      # 执行
    "arbitration",      # 仲裁
    "preservation",     # 保全
    "nonlit",           # 非诉工作包
    "other",
)

MATTER_STATUSES = ("pending", "active", "done", "cancelled")


class Matter(Base):
    """事项：案件下的一个程序阶段或工作包。

    为什么不把阶段做成案件上的一个字段：一个案子可能同时在跑保全和一审，
    二审期间还要处理执行异议 —— 阶段不是单值。而且每个阶段有各自的
    时限、文书、工时，必须能独立挂载。
    """

    __tablename__ = "matter"
    __table_args__ = _table_args(
        Index("ix_matter_case", "case_id"),
        Index("ix_matter_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("case"), ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="other", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    court: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    external_no: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    opened_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    closed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
