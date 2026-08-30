"""内核自己的表。声明成 ORM 模型，autogenerate 才不会把它当成"要删掉的表"。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base


class ModuleState(Base):
    """启动时记录当前装载了哪些模块及其版本。"""

    __tablename__ = "module_state"
    __table_args__ = {"schema": "kernel"}

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    """审计日志。见 kernel/audit.py。

    放内核而不是某个业务模块 —— 所有模块都要写审计，不能都去依赖 identity。
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_id", "id"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
        Index("ix_audit_actor", "actor_id"),
        {"schema": "kernel"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 谁做的。系统自动行为可为空
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: 形如 identity.promote / case.view_cross_scope
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 本次访问用的数据范围（self/dept/dept_tree/all），用于识别越权查看
    scope: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
