from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from kernel import audit, security
from kernel.db.session import get_session
from kernel.errors import PermissionDenied
from kernel.registry import registry
from kernel.security import Principal
from modules.case import service
from modules.case.models import Case

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class CauseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: Optional[int]
    code: str
    name: str
    category: str
    workflow_template: str
    sort_order: int
    is_active: bool


class CauseIn(BaseModel):
    code: str
    name: str
    category: str = "civil"
    parent_id: Optional[int] = None
    workflow_template: str = ""
    sort_order: int = 0


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    title: str
    cause_id: Optional[int]
    client_id: Optional[int]
    status: str
    lead_id: int
    org_unit_id: Optional[int]
    court: str
    external_no: str
    amount: Optional[Decimal]
    opened_on: Optional[date]
    closed_on: Optional[date]
    summary: str


class CasePartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    party_id: int
    role: str
    is_ours: bool
    note: str


class CaseMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    role: str
    joined_on: date
    left_on: Optional[date]


class MatterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str
    status: str
    court: str
    external_no: str
    opened_on: Optional[date]
    closed_on: Optional[date]
    note: str


class StatusLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_status: str
    to_status: str
    actor_id: Optional[int]
    note: str


class CaseDetailOut(CaseOut):
    parties: List[CasePartyOut]
    members: List[CaseMemberOut]
    matters: List[MatterOut]
    history: List[StatusLogOut]
    #: 当前状态下允许进入的下一个状态 —— 前端据此决定显示哪些按钮
    next_statuses: List[str]


class CaseIn(BaseModel):
    #: ★ 留空自动生成 AJ2026-0001。律所有自己的编号规则就手工填。
    code: str = ""
    title: str
    lead_id: Optional[int] = Field(default=None, description="留空则记在自己名下")
    cause_id: Optional[int] = None
    client_id: Optional[int] = None
    org_unit_id: Optional[int] = None
    court: str = ""
    external_no: str = ""
    amount: Optional[Decimal] = None
    opened_on: Optional[date] = None
    summary: str = ""


class TransitionIn(BaseModel):
    to_status: str
    note: str = ""


class PartyIn(BaseModel):
    party_id: int
    role: str
    is_ours: bool = False
    note: str = ""


class MemberIn(BaseModel):
    user_id: int
    role: str = "member"


class MatterIn(BaseModel):
    name: str
    kind: str = "other"
    court: str = ""
    external_no: str = ""
    opened_on: Optional[date] = None
    note: str = ""


class ReassignIn(BaseModel):
    new_lead_id: int
    new_org_unit_id: Optional[int] = None


# ---------------------------------------------------------------- 案由字典


@router.get("/causes", response_model=List[CauseOut])
def list_causes(
    principal: Principal = Depends(require_perm("case.view")),
    session: Session = Depends(get_session),
) -> List[CauseOut]:
    return [CauseOut.model_validate(c)
            for c in service.list_causes(session, principal.tenant_id)]


@router.post("/causes", response_model=CauseOut)
def create_cause(
    payload: CauseIn,
    request: Request,
    principal: Principal = Depends(require_perm("case.manage_cause")),
    session: Session = Depends(get_session),
) -> CauseOut:
    cause = service.create_cause(
        session, principal.tenant_id, payload.code, payload.name,
        category=payload.category, parent_id=payload.parent_id,
        workflow_template=payload.workflow_template, sort_order=payload.sort_order,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="case.create_cause", resource_type="cause",
                resource_id=str(cause.id), ip=client_ip(request),
                detail=payload.model_dump())
    return CauseOut.model_validate(cause)


# ---------------------------------------------------------------- 案件


@router.get("/cases", response_model=List[CaseOut])
def list_cases(
    keyword: str = Query(default=""),
    status: str = Query(default=""),
    f: str = Query(default="", description="看板口径：open / new / closed"),
    mine: bool = Query(default=False, description="只看我主办的"),
    org_id: int = Query(default=0, description="只看某个团队的"),
    since: Optional[date] = Query(default=None),
    until: Optional[date] = Query(default=None),
    principal: Principal = Depends(require_perm("case.view")),
    session: Session = Depends(get_session),
) -> List[CaseOut]:
    """
    ★ `f` / `mine` / `since` 这几个参数是给**看板点进来**用的，
      而且必须和 `case.metrics()` 里的口径**逐字一致**。

      不一致会出一个特别难查的错：工作台写"在办 4 件"，点进去列出 11 件。
      用户不会觉得是"筛选条件不同"，只会觉得**统计算错了** ——
      然后整个看板就不可信了。所以这里的 open / new / closed
      与 service.metrics 用的是同一组常量、同一套日期判断。
    """
    # 数据范围 + 隔离墙，一并由 service 套上
    stmt = service.scoped_cases(session, principal)
    if keyword.strip():
        like = "%{0}%".format(keyword.strip())
        stmt = stmt.where(or_(Case.title.ilike(like), Case.code.ilike(like),
                              Case.external_no.ilike(like)))
    if status.strip():
        stmt = stmt.where(Case.status == status.strip())

    if mine:
        stmt = stmt.where(Case.lead_id == principal.user_id)
    if org_id:
        stmt = stmt.where(Case.org_unit_id == org_id)

    f = f.strip()
    if f == "open":
        stmt = stmt.where(Case.status.in_(service.OPEN_STATUSES))
    elif f == "new":
        if since:
            stmt = stmt.where(Case.opened_on >= since)
        if until:
            stmt = stmt.where(Case.opened_on <= until)
    elif f == "closed":
        stmt = stmt.where(Case.closed_on.is_not(None))
        if since:
            stmt = stmt.where(Case.closed_on >= since)
        if until:
            stmt = stmt.where(Case.closed_on <= until)

    return [CaseOut.model_validate(c)
            for c in session.scalars(stmt.order_by(Case.id.desc()))]


@router.get("/cases/{case_id}", response_model=CaseDetailOut)
def case_detail(
    case_id: int,
    principal: Principal = Depends(require_perm("case.view")),
    session: Session = Depends(get_session),
) -> CaseDetailOut:
    case = service.get_case(session, case_id)
    _assert_visible(session, principal, case)
    return CaseDetailOut(
        **CaseOut.model_validate(case).model_dump(),
        parties=[CasePartyOut.model_validate(p) for p in service.parties_of(session, case_id)],
        members=[CaseMemberOut.model_validate(m) for m in service.members_of(session, case_id)],
        matters=[MatterOut.model_validate(m) for m in service.matters_of(session, case_id)],
        history=[StatusLogOut.model_validate(h) for h in service.status_history(session, case_id)],
        next_statuses=list(service.ALLOWED_TRANSITIONS.get(case.status, ())),
    )


@router.post("/cases", response_model=CaseOut)
def create_case(
    payload: CaseIn,
    request: Request,
    principal: Principal = Depends(require_perm("case.manage")),
    session: Session = Depends(get_session),
) -> CaseOut:
    # ★ 留空自动生成 —— 逼用户想编号，填出来的就是「1」「测试」
    code = payload.code.strip() or service.next_case_code(
        session, principal.tenant_id)
    case = service.create_case(
        session, principal.tenant_id, code, payload.title,
        lead_id=payload.lead_id or principal.user_id,
        cause_id=payload.cause_id, client_id=payload.client_id,
        org_unit_id=payload.org_unit_id, court=payload.court,
        external_no=payload.external_no, amount=payload.amount,
        opened_on=payload.opened_on, summary=payload.summary,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="case.create", resource_type="case", resource_id=str(case.id),
                ip=client_ip(request), detail={"code": case.code, "title": case.title})
    return CaseOut.model_validate(case)


@router.post("/cases/{case_id}/transition", response_model=CaseOut)
def transition(
    case_id: int,
    payload: TransitionIn,
    request: Request,
    principal: Principal = Depends(require_perm("case.manage")),
    session: Session = Depends(get_session),
) -> CaseOut:
    """推进案件状态。**结案与终止是不可逆动作，另外要求 case.close。**"""
    case = service.get_case(session, case_id)
    _assert_visible(session, principal, case)
    if payload.to_status in ("closed", "archived", "terminated"):
        security.require(principal, "case.close")

    before = case.status
    service.transition(session, case_id, payload.to_status,
                       actor_id=principal.user_id, note=payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="case.transition", resource_type="case", resource_id=str(case_id),
                ip=client_ip(request),
                detail={"from": before, "to": payload.to_status, "note": payload.note})
    return CaseOut.model_validate(case)


@router.post("/cases/{case_id}/reassign", response_model=CaseOut)
def reassign(
    case_id: int,
    payload: ReassignIn,
    request: Request,
    principal: Principal = Depends(require_perm("case.manage")),
    session: Session = Depends(get_session),
) -> CaseOut:
    case = service.get_case(session, case_id)
    _assert_visible(session, principal, case)
    before = {"lead_id": case.lead_id, "org_unit_id": case.org_unit_id}
    service.reassign_lead(session, case_id, payload.new_lead_id, payload.new_org_unit_id)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="case.reassign", resource_type="case", resource_id=str(case_id),
                ip=client_ip(request), detail={"before": before, "after": payload.model_dump()})
    return CaseOut.model_validate(case)


# ---------------------------------------------------------------- 当事人 / 承办人 / 事项


@router.post("/cases/{case_id}/parties", response_model=CasePartyOut)
def add_party(
    case_id: int,
    payload: PartyIn,
    principal: Principal = Depends(require_perm("case.manage")),
    session: Session = Depends(get_session),
) -> CasePartyOut:
    case = service.get_case(session, case_id)
    _assert_visible(session, principal, case)
    entry = service.add_party(
        session, principal.tenant_id, case_id, payload.party_id,
        payload.role, is_ours=payload.is_ours, note=payload.note,
    )
    return CasePartyOut.model_validate(entry)


@router.post("/cases/{case_id}/members", response_model=CaseMemberOut)
def add_member(
    case_id: int,
    payload: MemberIn,
    principal: Principal = Depends(require_perm("case.manage")),
    session: Session = Depends(get_session),
) -> CaseMemberOut:
    case = service.get_case(session, case_id)
    _assert_visible(session, principal, case)
    member = service.add_member(session, principal.tenant_id, case_id,
                                payload.user_id, role=payload.role)
    return CaseMemberOut.model_validate(member)


@router.post("/cases/{case_id}/matters", response_model=MatterOut)
def create_matter(
    case_id: int,
    payload: MatterIn,
    principal: Principal = Depends(require_perm("case.manage")),
    session: Session = Depends(get_session),
) -> MatterOut:
    case = service.get_case(session, case_id)
    _assert_visible(session, principal, case)
    matter = service.create_matter(
        session, principal.tenant_id, case_id, payload.name, kind=payload.kind,
        court=payload.court, external_no=payload.external_no,
        opened_on=payload.opened_on, note=payload.note,
    )
    return MatterOut.model_validate(matter)


# ---------------------------------------------------------------- 内部


def _assert_visible(session: Session, principal: Principal, case: Case) -> None:
    """单条访问也要过数据范围 —— 列表过滤了但详情没过，是常见的越权入口。"""
    service.assert_visible(session, principal, case.id)


CaseDetailOut.model_rebuild()
