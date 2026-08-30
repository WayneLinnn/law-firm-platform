from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.conflict import service

router = APIRouter()

identity = registry.api("identity")
case_api = registry.api("case")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class HitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    level: str
    kind: str
    subject_party_id: int
    matched_party_id: Optional[int]
    related_case_id: Optional[int]
    related_client_id: Optional[int]
    description: str
    resolution: str
    resolved: bool


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    status: str
    requested_by: Optional[int]
    requested_at: datetime
    decided_by: Optional[int]
    decided_at: Optional[datetime]
    decision_note: str


class CheckDetailOut(CheckOut):
    hits: List[HitOut]
    snapshot: str


class WallIn(BaseModel):
    user_id: int
    mode: str = "deny"
    reason: str = "利冲隔离"


class DecideIn(BaseModel):
    status: str = Field(description="waived 放行 / blocked 阻断")
    note: str = Field(description="必须写明理由 —— 事后追责要靠它")
    walls: List[WallIn] = Field(default_factory=list,
                                description="放行时可顺带设隔离墙")


class ResolveIn(BaseModel):
    resolution: str


# ---------------------------------------------------------------- 接口


@router.post("/cases/{case_id}/check", response_model=CheckDetailOut)
def run_check(
    case_id: int,
    request: Request,
    principal: Principal = Depends(require_perm("conflict.run")),
    session: Session = Depends(get_session),
) -> CheckDetailOut:
    """跑一次利冲检查。同一案件可重复跑，每次留一条记录。"""
    _assert_case_visible(session, principal, case_id)
    check = service.run_check(session, principal.tenant_id, case_id,
                              requested_by=principal.user_id)
    hits = service.hits_of(session, check.id)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="conflict.run", resource_type="case", resource_id=str(case_id),
                ip=client_ip(request),
                detail={"check_id": check.id, "status": check.status,
                        "hits": len(hits),
                        "levels": sorted({h.level for h in hits})})
    return _detail(check, hits)


@router.get("/cases/{case_id}/checks", response_model=List[CheckOut])
def list_checks(
    case_id: int,
    principal: Principal = Depends(require_perm("conflict.view")),
    session: Session = Depends(get_session),
) -> List[CheckOut]:
    _assert_case_visible(session, principal, case_id)
    return [CheckOut.model_validate(c) for c in service.checks_of_case(session, case_id)]


@router.get("/cases/{case_id}/latest", response_model=Optional[CheckDetailOut])
def latest(
    case_id: int,
    principal: Principal = Depends(require_perm("conflict.view")),
    session: Session = Depends(get_session),
) -> Optional[CheckDetailOut]:
    _assert_case_visible(session, principal, case_id)
    check = service.latest_check(session, case_id)
    if check is None:
        return None
    return _detail(check, service.hits_of(session, check.id))


@router.post("/checks/{check_id}/decide", response_model=CheckDetailOut)
def decide(
    check_id: int,
    payload: DecideIn,
    request: Request,
    principal: Principal = Depends(require_perm("conflict.decide")),
    session: Session = Depends(get_session),
) -> CheckDetailOut:
    """管委会处置。**放行必须写理由**，可顺带设隔离墙。"""
    check = service.get_check(session, check_id)
    _assert_case_visible(session, principal, check.case_id)
    service.decide(
        session, check_id, payload.status, principal.user_id, payload.note,
        walls=[w.model_dump() for w in payload.walls],
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="conflict.decide", resource_type="case",
                resource_id=str(check.case_id), ip=client_ip(request),
                detail={"check_id": check_id, "status": payload.status,
                        "note": payload.note,
                        "walls": [w.model_dump() for w in payload.walls]})
    return _detail(check, service.hits_of(session, check_id))


@router.post("/hits/{hit_id}/resolve", response_model=HitOut)
def resolve_hit(
    hit_id: int,
    payload: ResolveIn,
    principal: Principal = Depends(require_perm("conflict.decide")),
    session: Session = Depends(get_session),
) -> HitOut:
    """单条处置。逐条给理由，比整体一句"已确认无冲突"更经得起检查。"""
    return HitOut.model_validate(service.resolve_hit(session, hit_id, payload.resolution))


# ---------------------------------------------------------------- 内部


def _detail(check, hits) -> CheckDetailOut:
    return CheckDetailOut(
        **CheckOut.model_validate(check).model_dump(),
        hits=[HitOut.model_validate(h) for h in hits],
        snapshot=check.snapshot,
    )


def _assert_case_visible(session: Session, principal: Principal, case_id: int) -> None:
    """利冲结果里含对方名称与关联案件，同样要过案件的数据范围。

    规则由 case 模块提供 —— 我们不去摸它的表（边界检查会拦）。
    """
    case_api.assert_visible(session, principal, case_id)


CheckDetailOut.model_rebuild()
