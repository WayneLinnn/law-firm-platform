# -*- coding: utf-8 -*-
"""法律咨询的 HTTP 口。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.consult import service

router = APIRouter()

# ★ 跨模块只能走 api.py —— 直接 import modules.identity.deps 会被边界检查拦下
identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


class ConsultIn(BaseModel):
    client_id: int
    happened_on: date
    minutes: int = Field(description="实际聊了多久（分钟）")
    summary: str = ""
    channel: str = "office"
    case_id: Optional[int] = None
    hourly_rate: Optional[Decimal] = Field(
        default=None, description="留空用本所费率")
    free: bool = False
    free_reason: str = ""


class ConsultPatch(BaseModel):
    happened_on: Optional[date] = None
    minutes: Optional[int] = None
    summary: Optional[str] = None
    channel: Optional[str] = None
    case_id: Optional[int] = None
    hourly_rate: Optional[Decimal] = None
    free: Optional[bool] = None
    free_reason: Optional[str] = None


@router.get("/consults")
def list_consults(
    client_id: int = Query(default=0),
    case_id: int = Query(default=0),
    since: Optional[date] = Query(default=None),
    until: Optional[date] = Query(default=None),
    status: str = Query(default=""),
    principal: Principal = Depends(require_perm("consult.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    rows = service.list_consults(
        session, principal,
        client_id=client_id or None, case_id=case_id or None,
        since=since, until=until, status=status)
    return [service.describe(r) for r in rows]


@router.post("/consults")
def create_consult(
    payload: ConsultIn,
    request: Request,
    principal: Principal = Depends(require_perm("consult.log")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    row = service.log(
        session, principal,
        client_id=payload.client_id, happened_on=payload.happened_on,
        minutes=payload.minutes, summary=payload.summary,
        channel=payload.channel, case_id=payload.case_id,
        hourly_rate=payload.hourly_rate,
        free=payload.free, free_reason=payload.free_reason,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="consult.log", resource_type="consult",
                resource_id=str(row.id), ip=client_ip(request),
                detail={"client_id": row.client_id, "minutes": row.minutes,
                        "amount": str(row.amount)})
    return service.describe(row)


@router.patch("/consults/{consult_id}")
def update_consult(
    consult_id: int,
    payload: ConsultPatch,
    principal: Principal = Depends(require_perm("consult.log")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    row = service.update(session, principal, consult_id,
                         **payload.model_dump(exclude_unset=True))
    return service.describe(row)


@router.post("/consults/{consult_id}/void")
def void_consult(
    consult_id: int,
    reason: str = Query(description="作废原因"),
    request: Request = None,
    principal: Principal = Depends(require_perm("consult.log")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    row = service.void(session, principal, consult_id, reason)
    return service.describe(row)


@router.get("/clients/{client_id}/summary")
def client_summary(
    client_id: int,
    principal: Principal = Depends(require_perm("consult.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """一个客户咨询过几次、总共多少钱。"""
    return service.by_client(session, principal, client_id)


@router.get("/rate")
def my_rate(
    principal: Principal = Depends(require_perm("consult.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """我的咨询费率，以及本所的计费口径。"""
    return {
        "hourly_rate": str(service.rate_for(session, principal)),
        "increment_minutes": service.BILLING_INCREMENT_MINUTES,
        "min_billable_minutes": service.MIN_BILLABLE_MINUTES,
    }
