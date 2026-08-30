"""审计日志。**放在内核而不是某个模块** —— 所有模块都要写审计，
不能让 case、billing 都去依赖 identity。

律所合规检查会看两类记录：
  1. 谁在什么时候看了不属于自己的东西（越权查看，合法但要留痕）
  2. 谁改了什么关键数据（晋升、利冲放行、隔离墙设置）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.db.models import AuditLog

logger = logging.getLogger(__name__)


def write(
    session: Session,
    *,
    tenant_id: int,
    actor_id: Optional[int],
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    scope: str = "",
    detail: Optional[Dict[str, Any]] = None,
    ip: str = "",
) -> AuditLog:
    """记一条审计。action 用 `模块.动作` 形式，如 identity.promote。"""
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        scope=scope,
        detail=json.dumps(detail or {}, ensure_ascii=False),
        ip=ip,
    )
    session.add(entry)
    session.flush()
    return entry


def read(
    session: Session,
    tenant_id: int,
    *,
    actor_id: Optional[int] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    limit: int = 100,
) -> List[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == str(resource_id))
    return list(session.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit)))
