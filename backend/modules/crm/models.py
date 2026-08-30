"""客户与当事人。全部落在 crm schema。

**核心设计：主体（Party）独立成表，是全所共享的主体库。**

为什么不能把"对方当事人"当成案件上的一个文本字段：

    利冲检查要回答的是 —— "我们要接的这个案子，对方是甲公司；
    所里有没有人正在代理甲公司？甲公司的母公司是不是我们的老客户？"

    这需要**跨案件、跨客户**地查同一个主体。如果对方名称只是案件表里的
    一个字符串，这个查询根本做不了；就算做了，"甲公司""甲公司（北京）"
    "北京甲公司"会被当成三个不同的主体，利冲直接漏掉。

三层结构：

    Party          主体库 —— 自然人或法人，全所唯一，靠证件号去重
    PartyRelation  主体间关系 —— 母子公司、法定代表人、配偶…利冲要顺着查
    Client         客户 —— 指向一个 Party，加上客户特有属性（案源、负责人）
    Contact        联系人 —— 客户那边具体对接的人

一个 Party 可以既是我们的客户，又在别的案子里是对方 —— 这正是利冲要抓的情况。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
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

SCHEMA = "crm"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


def _fk(table: str) -> str:
    return "{0}.{1}.id".format(SCHEMA, table)


# ---------------------------------------------------------------- 主体库


#: 自然人 / 法人或其他组织
PARTY_KINDS = ("person", "org")


class Party(Base):
    """主体：一个自然人或一个组织。**全所共享，利冲检查的基本单位。**

    去重靠 id_no（身份证号 / 统一社会信用代码）—— 有证件号的强制唯一，
    没有的靠名称提示可能重复。宁可多提示一次，也不能漏掉利冲。
    """

    __tablename__ = "party"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "id_no", name="uq_party_tenant_idno"),
        Index("ix_party_tenant_name", "tenant_id", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="org")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 身份证号 / 统一社会信用代码。**留空表示未知**，唯一约束对 NULL 不生效
    id_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: 曾用名、简称、英文名 —— 换行分隔。利冲检索会一并匹配
    aliases: Mapped[str] = mapped_column(Text, default="", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


#: 主体之间的关系。利冲检查要顺着这些边往外查一层。
PARTY_RELATIONS = (
    "parent",        # 母公司
    "subsidiary",    # 子公司
    "shareholder",   # 股东
    "legal_rep",     # 法定代表人
    "affiliate",     # 关联方
    "spouse",        # 配偶
    "relative",      # 亲属
)


class PartyRelation(Base):
    """主体关联。**利冲不能只查名字本身，要查到关联方。**

    真实场景：我们代理甲公司；对方是乙公司；乙公司是甲公司的全资子公司 ——
    这是利冲。只比对名称查不出来。
    """

    __tablename__ = "party_relation"
    __table_args__ = _table_args(
        UniqueConstraint("from_party_id", "to_party_id", "relation",
                         name="uq_party_relation"),
        Index("ix_relation_from", "from_party_id"),
        Index("ix_relation_to", "to_party_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    from_party_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("party"), ondelete="CASCADE"), nullable=False
    )
    to_party_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("party"), ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 客户


#: 潜在 / 正式 / 停止合作
CLIENT_STATUSES = ("prospect", "active", "inactive")


class Client(Base):
    """客户：我们代理的那一方。指向主体库里的一个 Party。

    owner_id（客户负责人）与 org_unit_id（归属团队）是数据范围过滤的依据 ——
    律师只看自己的客户，团队负责人看整个团队的。
    """

    __tablename__ = "client"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "code", name="uq_client_tenant_code"),
        UniqueConstraint("tenant_id", "party_id", name="uq_client_tenant_party"),
        Index("ix_client_owner", "owner_id"),
        Index("ix_client_org", "org_unit_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    party_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("party"), ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="prospect", nullable=False)
    #: 案源：谁带来的、怎么来的
    source: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    #: 客户负责人（identity.user.id）—— SELF 数据范围看这个
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 归属团队（identity.org_unit.id）—— DEPT / DEPT_TREE 数据范围看这个
    org_unit_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    onboarded_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Contact(Base):
    """客户那边的对接人。"""

    __tablename__ = "contact"
    __table_args__ = _table_args(Index("ix_contact_client", "client_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("client"), ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
