from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.billing import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class RateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    position_code: str
    user_id: Optional[int]
    hourly_rate: Decimal
    currency: str
    valid_from: date
    valid_to: Optional[date]
    note: str


class RateIn(BaseModel):
    hourly_rate: Decimal = Field(gt=0, description="元 / 小时")
    position_code: str = ""
    user_id: Optional[int] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    note: str = ""


class EngagementIn(BaseModel):
    case_id: int
    mode: str = Field(description="hourly / fixed / contingency / hybrid / retainer")
    client_id: Optional[int] = None
    fixed_amount: Optional[Decimal] = None
    #: 0.2 表示 20%。★ 法定上限 30%
    contingency_rate: Optional[Decimal] = None
    contingency_base: Optional[Decimal] = None
    base_amount: Optional[Decimal] = None
    cap_amount: Optional[Decimal] = None
    #: 按次咨询计费时谈定的单价（元/小时）。空 = 用本所费率表
    consult_rate: Optional[Decimal] = None
    tax_included: bool = True
    vat_rate: Optional[Decimal] = None
    contract_no: str = ""
    signed_on: Optional[date] = None
    note: str = ""
    #: 案由部分禁止风险代理时，律师确认本案诉请不在禁区里
    confirm_legality: bool = False


class InvoiceIn(BaseModel):
    case_id: Optional[int] = None
    client_id: Optional[int] = None
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    due_on: Optional[date] = None
    note: str = ""


class LineIn(BaseModel):
    description: str
    amount: Decimal
    kind: str = "fee"
    user_id: Optional[int] = None
    minutes: int = 0
    rate: Optional[Decimal] = None
    worked_on: Optional[date] = None


class PullIn(BaseModel):
    since: Optional[date] = None
    until: Optional[date] = None
    #: 账单没挂案件时，指明拉哪个客户的咨询
    client_id: Optional[int] = None


class FixedIn(BaseModel):
    amount: Optional[Decimal] = None
    description: str = ""


class ContingencyIn(BaseModel):
    recovered: Optional[Decimal] = Field(
        default=None, description="本案实际收回的金额；不填则用约定标的额")
    description: str = ""


class IssueIn(BaseModel):
    issued_on: Optional[date] = None
    due_on: Optional[date] = None


class PaymentIn(BaseModel):
    amount: Decimal = Field(gt=0)
    paid_on: Optional[date] = None
    method: str = "transfer"
    reference: str = Field(default="", description="银行流水号 —— 对账时唯一能核的东西")
    note: str = ""


class ReasonIn(BaseModel):
    reason: str


# ---------------------------------------------------------------- 费率


@router.get("/rates", response_model=List[RateOut])
def list_rates(
    on: Optional[date] = Query(default=None),
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> List[RateOut]:
    return [RateOut.model_validate(r)
            for r in service.list_rates(session, principal, on)]


@router.post("/rates", response_model=RateOut)
def create_rate(
    payload: RateIn,
    request: Request,
    principal: Principal = Depends(require_perm("billing.manage_rate")),
    session: Session = Depends(get_session),
) -> RateOut:
    row = service.set_rate(session, principal, **payload.model_dump())
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="billing.rate_set", resource_type="rate",
                resource_id=str(row.id), ip=client_ip(request),
                detail={"rate": str(payload.hourly_rate),
                        "position": payload.position_code,
                        "user_id": payload.user_id})
    return RateOut.model_validate(row)


# ---------------------------------------------------------------- 收费约定


@router.get("/cases/{case_id}/contingency-check")
def contingency_check(
    case_id: int,
    rate: float = Query(default=0.0, description="拟定的风险比例，如 0.2"),
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """★ 这个案子能不能做风险代理、最高收多少。

    **在签合同之前问这一句**，不是签完才发现。返回的 level：
      block   整类禁止，签不了
      confirm 部分禁止，要律师确认诉请不在禁区
      allow   没匹配到已知禁区（但 30% 上限照样管）
    """
    return service.check_contingency(
        session, principal, case_id,
        Decimal(str(rate)) if rate else None)


@router.get("/cases/{case_id}/engagement")
def get_engagement(
    case_id: int,
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> Optional[Dict[str, Any]]:
    e = service.engagement_of(session, principal, case_id)
    return service.describe_engagement(e) if e else None


@router.post("/engagements")
def create_engagement(
    payload: EngagementIn,
    request: Request,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """签收费约定。风险代理的合法性在这里硬拦。"""
    row = service.create_engagement(session, principal, **payload.model_dump())
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="billing.engagement_signed", resource_type="case",
                resource_id=str(payload.case_id), ip=client_ip(request),
                detail={"mode": payload.mode,
                        "contingency_rate": str(payload.contingency_rate or ""),
                        "confirmed": payload.confirm_legality})
    return service.describe_engagement(row)


# ---------------------------------------------------------------- 账单


@router.post("/invoices")
def create_invoice(
    payload: InvoiceIn,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.describe_invoice(
        service.create_invoice(session, principal, **payload.model_dump()))


@router.get("/cases/{case_id}/invoices")
def case_invoices(
    case_id: int,
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [service.describe_invoice(i)
            for i in service.invoices_of_case(session, principal, case_id)]


@router.get("/invoices/{invoice_id}/lines")
def invoice_lines(
    invoice_id: int,
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [service.describe_line(l)
            for l in service.lines_of(session, principal, invoice_id)]


@router.post("/invoices/{invoice_id}/lines")
def add_line(
    invoice_id: int,
    payload: LineIn,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.describe_line(service.add_line(
        session, principal, invoice_id, **payload.model_dump()))


@router.delete("/lines/{line_id}")
def remove_line(
    line_id: int,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.describe_invoice(
        service.remove_line(session, principal, line_id))


@router.post("/invoices/{invoice_id}/pull-consult")
def pull_consult(
    invoice_id: int,
    payload: PullIn,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """把还没出账的咨询费拉进账单。

    ★ 金额在记咨询那一刻就算好钉死了，这里不重算 —— 费率以后调价，
      去年那条咨询不该跟着变。
    """
    return service.pull_consult(session, principal, invoice_id,
                                **payload.model_dump())


@router.post("/invoices/{invoice_id}/pull-disbursements")
def pull_disbursements(
    invoice_id: int,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """把还没开票的代垫费用拉进账单。

    ★ 这些代垫多半是 approval（Step 18）的请款单付完款自动挂上来的 ——
      律师不用记着三个月前垫过什么。
    """
    return service.pull_disbursements(session, principal, invoice_id)


@router.get("/cases/{case_id}/disbursements")
def case_disbursements(
    case_id: int,
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """这个案子上垫了、还没开票的代垫费用。"""
    return [service.describe_disbursement(d)
            for d in service.pending_disbursements(session, principal, case_id)]


@router.post("/invoices/{invoice_id}/bill-fixed")
def bill_fixed(
    invoice_id: int,
    payload: FixedIn,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.describe_line(service.bill_fixed(
        session, principal, invoice_id, payload.amount, payload.description))


@router.post("/invoices/{invoice_id}/bill-contingency")
def bill_contingency(
    invoice_id: int,
    payload: ContingencyIn,
    principal: Principal = Depends(require_perm("billing.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.describe_line(service.bill_contingency(
        session, principal, invoice_id, payload.recovered, payload.description))


@router.post("/invoices/{invoice_id}/issue")
def issue(
    invoice_id: int,
    payload: IssueIn,
    request: Request,
    principal: Principal = Depends(require_perm("billing.issue")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """开出账单。**之后就锁死了**，同时把关联咨询推成已出账。"""
    inv = service.issue(session, principal, invoice_id,
                        payload.issued_on, payload.due_on)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="billing.invoice_issued", resource_type="case",
                resource_id=str(inv.case_id or ""), ip=client_ip(request),
                detail={"invoice": inv.number, "total": str(inv.total_amount)})
    return service.describe_invoice(inv)


@router.post("/invoices/{invoice_id}/void")
def void_invoice(
    invoice_id: int,
    payload: ReasonIn,
    request: Request,
    principal: Principal = Depends(require_perm("billing.issue")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """红冲（不是删除）—— 一张凭空消失的账单比一张错的更可疑。"""
    inv = service.void_invoice(session, principal, invoice_id, payload.reason)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="billing.invoice_voided", resource_type="case",
                resource_id=str(inv.case_id or ""), ip=client_ip(request),
                detail={"invoice": inv.number, "reason": payload.reason})
    return service.describe_invoice(inv)


# ---------------------------------------------------------------- 回款


@router.get("/invoices/{invoice_id}/payments")
def list_payments(
    invoice_id: int,
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [service.describe_payment(p)
            for p in service.payments_of(session, principal, invoice_id)]


@router.post("/invoices/{invoice_id}/payments")
def record_payment(
    invoice_id: int,
    payload: PaymentIn,
    request: Request,
    principal: Principal = Depends(require_perm("billing.payment")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """登记回款。★ 开了账单不等于收到钱。"""
    row = service.record_payment(session, principal, invoice_id,
                                 **payload.model_dump())
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="billing.payment_recorded", resource_type="invoice",
                resource_id=str(invoice_id), ip=client_ip(request),
                detail={"amount": str(payload.amount),
                        "reference": payload.reference})
    return service.describe_payment(row)


@router.post("/payments/{payment_id}/reverse")
def reverse_payment(
    payment_id: int,
    payload: ReasonIn,
    request: Request,
    principal: Principal = Depends(require_perm("billing.payment")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    row = service.reverse_payment(session, principal, payment_id, payload.reason)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="billing.payment_reversed", resource_type="invoice",
                resource_id=str(row.invoice_id), ip=client_ip(request),
                detail={"amount": str(row.amount), "reason": payload.reason})
    return service.describe_payment(row)


# ---------------------------------------------------------------- 汇总


@router.get("/cases/{case_id}/summary")
def case_summary(
    case_id: int,
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.case_billing(session, principal, case_id)


@router.get("/receivables")
def receivables(
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """应收账款与账龄。★ 律所真正的风险是开了单收不回来。"""
    return service.receivables(session, principal)


@router.get("/revenue-flow")
def revenue_flow(
    scope: str = Query(default="mine", description="mine / team / firm"),
    org_id: int = Query(default=0),
    since: Optional[date] = Query(default=None),
    until: Optional[date] = Query(default=None),
    principal: Principal = Depends(require_perm("billing.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """本期开票与回款的**逐笔明细** —— 看板上那两个数点开看到的东西。

    ★ 范围参数要和看板给的一模一样，否则明细加起来对不上格子里的数。
    """
    spec: Dict[str, Any] = {"since": since, "until": until}
    if scope == "mine":
        spec["user_id"] = principal.user_id
    elif scope == "team" and org_id:
        spec["org_id"] = org_id
    return service.revenue_flow(session, principal, spec)
