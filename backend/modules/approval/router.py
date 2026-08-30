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
from modules.approval import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class TicketIn(BaseModel):
    kind: str = Field(description="payment 请款 / reimburse 报销 / seal 用印 / disburse 付款")
    #: ★ 这一栏**财务、行政、用印管理都看得到** —— 不要写案件名称、当事人
    subject: str = Field(description="事由（一句话）。★ 不要写案件名称或当事人")
    amount: Decimal = Decimal("0")
    case_id: Optional[int] = None
    payee: str = ""
    payee_account: str = ""
    need_by: Optional[date] = None
    detail: str = ""
    doc_ids: str = ""
    # 用印专用
    seal_kind: str = ""
    seal_document: str = ""
    seal_copies: int = 0
    seal_takeaway: bool = False


class DecideIn(BaseModel):
    approve: bool
    note: str = Field(default="", description="驳回时必填")


class PayIn(BaseModel):
    paid_on: Optional[date] = None
    reference: str = Field(default="", description="银行流水号")


class SealIn(BaseModel):
    used_copies: int = Field(gt=0, description="★ 实际盖了几份，超过申请份数会被拦")
    note: str = ""


class ReasonIn(BaseModel):
    reason: str


class FlowIn(BaseModel):
    kind: str
    name: str
    steps: str = Field(description='形如 "团队负责人:approval.approve_team|管委会:approval.approve_firm"')
    min_amount: Decimal = Decimal("0")
    max_amount: Optional[Decimal] = None
    note: str = ""


# ---------------------------------------------------------------- 流转规则


@router.get("/flows")
def list_flows(
    kind: str = Query(default=""),
    principal: Principal = Depends(require_perm("approval.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [service.describe_flow(r)
            for r in service.list_flows(session, principal, kind)]


@router.post("/flows/seed")
def seed_flows(
    principal: Principal = Depends(require_perm("approval.manage_flow")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """把内置流转链灌进本所。已存在的不覆盖 —— 律所可能改过门槛。"""
    return [service.describe_flow(r)
            for r in service.ensure_default_flows(session, principal.tenant_id)]


@router.post("/flows")
def create_flow(
    payload: FlowIn,
    request: Request,
    principal: Principal = Depends(require_perm("approval.manage_flow")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    row = service.create_flow(session, principal, **payload.model_dump())
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="approval.flow_created", resource_type="flow",
                resource_id=str(row.id), ip=client_ip(request),
                detail={"kind": payload.kind, "steps": payload.steps})
    return service.describe_flow(row)


# ---------------------------------------------------------------- 单据


@router.post("/tickets")
def create_ticket(
    payload: TicketIn,
    principal: Principal = Depends(require_perm("approval.request")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """提一张单据（草稿）。

    ★ 事由里出现案件名称或当事人会被拦下来 —— 这一栏会跟着单子
      在所里流转，处理这笔钱的人不需要知道是哪个案子。
    """
    row = service.create_ticket(session, principal, **payload.model_dump())
    return service.describe(session, principal, row)


@router.post("/tickets/{ticket_id}/submit")
def submit(
    ticket_id: int,
    request: Request,
    principal: Principal = Depends(require_perm("approval.request")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """提交。★ 流转链在这一刻固化，之后规则改了也不影响这张单。"""
    row = service.submit(session, principal, ticket_id)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="approval.submitted", resource_type="ticket",
                resource_id=str(ticket_id), ip=client_ip(request),
                detail={"number": row.number, "amount": str(row.amount)})
    return service.describe(session, principal, row)


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    principal: Principal = Depends(require_perm("approval.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """★ 返回的内容按**你能不能看这个案子**脱敏，并会明说脱敏了。"""
    row = service._get(session, principal, ticket_id)
    out = service.describe(session, principal, row)
    out["steps"] = [service.describe_step(s)
                    for s in service.steps_of(session, principal, ticket_id)]
    return out


@router.post("/tickets/{ticket_id}/decide")
def decide(
    ticket_id: int,
    payload: DecideIn,
    request: Request,
    principal: Principal = Depends(require_perm("approval.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """批 / 驳当前这一道。具体要什么权限由这一道自己定。"""
    row = service.decide(session, principal, ticket_id,
                         payload.approve, payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="approval.decided", resource_type="ticket",
                resource_id=str(ticket_id), ip=client_ip(request),
                detail={"approve": payload.approve, "note": payload.note})
    return service.describe(session, principal, row)


@router.post("/tickets/{ticket_id}/pay")
def pay(
    ticket_id: int,
    payload: PayIn,
    request: Request,
    principal: Principal = Depends(require_perm("approval.pay")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """财务付款，并把代垫回写到 billing。"""
    row = service.pay(session, principal, ticket_id,
                      payload.paid_on, payload.reference)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="approval.paid", resource_type="ticket",
                resource_id=str(ticket_id), ip=client_ip(request),
                detail={"number": row.number, "amount": str(row.amount),
                        "reference": payload.reference})
    return service.describe(session, principal, row)


@router.post("/tickets/{ticket_id}/seal")
def execute_seal(
    ticket_id: int,
    payload: SealIn,
    request: Request,
    principal: Principal = Depends(require_perm("approval.seal")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """用印执行，回填实际份数。★ 超过申请份数会被拦。"""
    row = service.execute_seal(session, principal, ticket_id,
                               payload.used_copies, payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="approval.sealed", resource_type="ticket",
                resource_id=str(ticket_id), ip=client_ip(request),
                detail={"number": row.number, "seal_kind": row.seal_kind,
                        "copies": row.seal_copies, "used": row.seal_used})
    return service.describe(session, principal, row)


@router.post("/tickets/{ticket_id}/cancel")
def cancel(
    ticket_id: int,
    payload: ReasonIn,
    request: Request,
    principal: Principal = Depends(require_perm("approval.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    row = service.cancel(session, principal, ticket_id, payload.reason)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="approval.cancelled", resource_type="ticket",
                resource_id=str(ticket_id), ip=client_ip(request),
                detail={"reason": payload.reason})
    return service.describe(session, principal, row)


# ---------------------------------------------------------------- 查询


@router.get("/mine")
def mine(
    status: str = Query(default=""),
    principal: Principal = Depends(require_perm("approval.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [service.describe(session, principal, t)
            for t in service.my_tickets(session, principal, status)]


@router.get("/inbox")
def inbox(
    principal: Principal = Depends(require_perm("approval.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """待我处理。★ 按**权限**算不按指派 —— 负责人出差不该让单子停摆。"""
    return [service.describe(session, principal, t)
            for t in service.inbox(session, principal)]


@router.get("/cases/{case_id}/tickets")
def case_tickets(
    case_id: int,
    principal: Principal = Depends(require_perm("approval.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [service.describe(session, principal, t)
            for t in service.tickets_of_case(session, principal, case_id)]
