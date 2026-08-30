# -*- coding: utf-8 -*-
"""账单的业务层。

**头号原则：算错一分钱，财务要找半天；算错一个百分点，可能是违规收费。**

这一层有三类不同性质的规则，界线必须清楚：

    法定的    风险代理禁区、30% 上限 —— **硬拦**，律所改不了
    约定的    费率、折扣、封顶、含不含税 —— 按合同来，系统只负责别算错
    事实的    咨询、代垫、回款 —— 记下来，不许事后悄悄改

**第二原则：发出去的数字要钉死。** 账单一旦开出（issued），
金额、费率、明细全部锁死。费率后来调了、咨询被作废了、税率变了，
都不能让一张已经在客户手里的账单跟着变。要更正只能红冲后重开。

★ 钱一律 Decimal。这不是洁癖：0.1 + 0.2 != 0.3 落在律师费上，
  就是账单和收款差一分钱，然后财务花半天去找。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.billing import rules as legal
from modules.billing.models import (
    DEFAULT_VAT_RATE,
    Disbursement,
    FEE_MODES,
    FEE_MODE_LABELS,
    INVOICE_STATUS_LABELS,
    LINE_KINDS,
    LOCKED_INVOICE_STATUSES,
    PAY_METHODS,
    Engagement,
    FeeRate,
    Invoice,
    InvoiceLine,
    Payment,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
#: 元，两位小数
CENT = Decimal("0.01")


def _case_api():
    return registry.api("case")


def _consult_api():
    """consult 是**可选**依赖：没装也能开固定费和风险代理的账单，
    只是开不了咨询费账单。

    ★ 原来这里是 time（工时）。提成制律所不按工时给客户开账单 ——
      团队成员和助理是工资制/分成制，他们的时间不换算成客户的钱。
      真正按小时收钱的只有法律咨询，所以只剩这一个。"""
    return registry.optional_api("consult")


def _visible(session: Session, principal: Principal, case_id: Optional[int]) -> None:
    if case_id:
        _case_api().assert_visible(session, principal, case_id)


def _writable(session: Session, principal: Principal, case_id) -> None:
    """写操作走这句：看得见 **且** 写得动。归档/终止的案子只读。"""
    if case_id:
        _case_api().assert_writable(session, principal, case_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def money(x) -> Decimal:
    """把任何东西变成两位小数的钱。

    ★ 一律 ROUND_HALF_UP（四舍五入），不用 Python 默认的
      ROUND_HALF_EVEN（银行家舍入）。理由很实际：客户拿计算器按出来的是
      四舍五入，**对不上的时候解释成本极高** —— 你没法跟客户讲
      "0.125 舍成 0.12 是因为 2 是偶数"。
    """
    return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


# ================================================================ 费率


def set_rate(session: Session, principal: Principal, hourly_rate,
             position_code: str = "", user_id: Optional[int] = None,
             valid_from: Optional[date] = None, valid_to: Optional[date] = None,
             note: str = "") -> FeeRate:
    """加一条费率。按职级或按人，二选一。"""
    if not principal.can("billing.manage_rate"):
        raise DomainError("维护费率需要 billing.manage_rate 权限",
                          code="billing.forbidden")
    if not position_code and not user_id:
        raise DomainError("费率要么挂在职级上，要么挂在某个人身上",
                          code="billing.rate_scope_required")
    rate = money(hourly_rate)
    if rate <= ZERO:
        raise DomainError("费率必须大于 0", code="billing.bad_rate")

    row = FeeRate(
        tenant_id=principal.tenant_id, position_code=position_code or "",
        user_id=user_id, hourly_rate=rate,
        valid_from=valid_from or date.today(), valid_to=valid_to, note=note,
    )
    session.add(row)
    session.flush()
    return row


def list_rates(session: Session, principal: Principal,
               on: Optional[date] = None) -> List[FeeRate]:
    stmt = select(FeeRate).where(FeeRate.tenant_id == principal.tenant_id)
    if on:
        stmt = stmt.where(FeeRate.valid_from <= on).where(
            (FeeRate.valid_to.is_(None)) | (FeeRate.valid_to >= on))
    return list(session.scalars(
        stmt.order_by(FeeRate.valid_from.desc(), FeeRate.id.desc())).all())


def rate_for(session: Session, tenant_id: int, user_id: int,
             position_code: str = "", on: Optional[date] = None) -> Optional[Decimal]:
    """查某人在某天的小时费率。

    ★ 一定要带日期。费率有生效期，账单按**当时**的价算 ——
      去年的咨询今年开账单，用的是去年的价。
      不带日期查费率，等于把调价倒灌进历史账单。

    ★ 个人特价盖过职级价 —— 有的人就是有特价，那是所里谈好的。
    """
    on = on or date.today()
    base = select(FeeRate).where(
        FeeRate.tenant_id == tenant_id,
        FeeRate.valid_from <= on,
        (FeeRate.valid_to.is_(None)) | (FeeRate.valid_to >= on),
    ).order_by(FeeRate.valid_from.desc(), FeeRate.id.desc())

    mine = session.scalars(base.where(FeeRate.user_id == user_id)).first()
    if mine is not None:
        return mine.hourly_rate
    if position_code:
        byp = session.scalars(base.where(
            FeeRate.position_code == position_code,
            FeeRate.user_id.is_(None))).first()
        if byp is not None:
            return byp.hourly_rate
    return None


# ================================================================ 收费约定


def check_contingency(session: Session, principal: Principal, case_id: int,
                      rate: Optional[Decimal]) -> Dict[str, Any]:
    """风险代理的合法性检查。**在签约时做，不是开账单时做。**

    返回 {"level", "basis", "reason", "caveat", "message"}；
    level == "block" 的调用方必须拒绝。

    ★ 为什么把"检查"和"拒绝"分开：界面上要能**先看到**为什么不行，
      而不是点了保存才弹一个红框。律师需要拿这段话去跟客户解释。
    """
    case = _case_api().get_case(session, case_id)
    cause = None
    if getattr(case, "cause_id", None):
        try:
            cause = _case_api().get_cause(session, case.cause_id)
        except Exception as exc:                         # noqa: BLE001
            logger.warning("取案由失败 case=%s: %s", case_id, exc)

    code = getattr(cause, "code", "") or ""
    category = getattr(cause, "category", "") or ""
    r = legal.rule_for(code, category)

    out = {
        "level": r["level"], "basis": r["basis"],
        "reason": r["reason"], "caveat": r["caveat"],
        "cause_code": code, "cause_name": getattr(cause, "name", "") or "",
        "max_rate": str(legal.CONTINGENCY_MAX_RATE),
        "rate_ok": True, "message": "",
    }

    # ★ 30% 上限**不看案由**，任何风险代理都管
    if rate is not None and rate > legal.CONTINGENCY_MAX_RATE:
        out["rate_ok"] = False
        out["level"] = "block"
        out["basis"] = legal.BASIS_ARTICLE_13
        out["reason"] = (
            "风险代理比例 {0}% 超过法定上限 {1}%。".format(
                (rate * 100).quantize(CENT), (legal.CONTINGENCY_MAX_RATE * 100)))
        out["caveat"] = ("上限的基数是**合同约定标的额**，且**不含代垫费用** —— "
                         "把诉讼费、鉴定费算进基数会把上限撑大，那是超收。")

    # ★ message 只放**实质内容**（禁的是什么、依据是哪条），不带引子。
    #   引子由调用方按场景加：界面上标题已经写了"这个案由部分禁止风险代理"，
    #   后端再说一遍，同一句话就连着出现两次 —— 读的人会以为哪里出错了。
    #   报错那条路没有标题，所以引子在 create_engagement 里补。
    if out["level"] in ("block", "confirm"):
        out["message"] = "{0}　依据：{1}".format(out["reason"], out["basis"])
    else:
        out["message"] = "未匹配到已知禁区。"
    return out


def create_engagement(session: Session, principal: Principal, case_id: int,
                      mode: str, client_id: Optional[int] = None,
                      fixed_amount=None, contingency_rate=None,
                      contingency_base=None, base_amount=None, cap_amount=None,
                      consult_rate=None,
                      tax_included: bool = True, vat_rate=None,
                      contract_no: str = "", signed_on: Optional[date] = None,
                      note: str = "", confirm_legality: bool = False) -> Engagement:
    """签一份收费约定。

    ★ 风险代理的合法性在这里硬拦。level=block 直接拒绝；
      level=confirm 要求 confirm_legality=True，并把提示内容**存档** ——
      收费违规被查时，"系统提示过、律师确认过"和"没人提过"是两种处境。
    """
    if not principal.can("billing.manage"):
        raise DomainError("签收费约定需要 billing.manage 权限",
                          code="billing.forbidden")
    _writable(session, principal, case_id)
    if mode not in FEE_MODES:
        raise DomainError(
            "收费模式只能是：{0}".format(
                "、".join("{0}（{1}）".format(k, FEE_MODE_LABELS[k]) for k in FEE_MODES)),
            code="billing.bad_mode")

    legality_note = ""
    crate = Decimal(str(contingency_rate)) if contingency_rate is not None else None

    if mode in ("contingency", "hybrid"):
        if crate is None or crate <= 0:
            raise DomainError("风险代理必须写明风险比例",
                              code="billing.contingency_rate_required")
        chk = check_contingency(session, principal, case_id, crate)
        if chk["level"] == "block":
            raise DomainError(chk["message"], code="billing.contingency_forbidden")
        if chk["level"] == "confirm" and not confirm_legality:
            # 报错这条路没有界面标题，引子要在这里补上
            raise DomainError(
                "这个案由**部分禁止**风险代理。\n" + chk["message"] +
                "\n确认本案诉请不含上述禁区后再提交（这一确认会记录在案）。",
                code="billing.contingency_needs_confirm")
        legality_note = "{0}\n依据：{1}\n盖不住：{2}".format(
            chk["reason"] or "未匹配到已知禁区", chk["basis"] or "—", chk["caveat"])

    if mode in ("fixed", "retainer") and not fixed_amount:
        raise DomainError("固定收费/顾问费必须写明金额",
                          code="billing.fixed_amount_required")
    if mode == "hybrid" and not base_amount:
        raise DomainError("混合收费必须写明基础费金额",
                          code="billing.base_amount_required")

    # 同一个案子只留一份生效的约定 —— 换了约定要把旧的停掉，
    # 否则开账单时不知道该按哪份算
    for old in session.scalars(select(Engagement).where(
            Engagement.tenant_id == principal.tenant_id,
            Engagement.case_id == case_id, Engagement.active.is_(True))).all():
        old.active = False

    row = Engagement(
        tenant_id=principal.tenant_id, case_id=case_id, client_id=client_id,
        mode=mode,
        fixed_amount=money(fixed_amount) if fixed_amount is not None else None,
        contingency_rate=crate,
        contingency_base=money(contingency_base) if contingency_base is not None else None,
        base_amount=money(base_amount) if base_amount is not None else None,
        cap_amount=money(cap_amount) if cap_amount is not None else None,
        consult_rate=money(consult_rate) if consult_rate is not None else None,
        tax_included=tax_included,
        vat_rate=Decimal(str(vat_rate)) if vat_rate is not None else DEFAULT_VAT_RATE,
        contract_no=contract_no, signed_on=signed_on, note=note,
        legality_note=legality_note,
        confirmed_by=principal.user_id if legality_note else None,
        confirmed_at=_now() if legality_note else None,
        created_by=principal.user_id,
    )
    session.add(row)
    session.flush()
    return row


def engagement_of(session: Session, principal: Principal,
                  case_id: int) -> Optional[Engagement]:
    _visible(session, principal, case_id)
    return session.scalar(select(Engagement).where(
        Engagement.tenant_id == principal.tenant_id,
        Engagement.case_id == case_id,
        Engagement.active.is_(True)))


# ================================================================ 税


def split_tax(gross_or_net: Decimal, vat_rate: Decimal,
              tax_included: bool) -> Tuple[Decimal, Decimal, Decimal]:
    """算税。返回 (不含税, 税额, 含税)。

    ★ 「这个价含不含税」是中国律所收费纠纷的前三名 —— 口头谈的时候
      谁也没说，开票的时候差 6%，两边都觉得对方赖账。所以这里不猜，
      由约定上的 tax_included 说了算，两个数都算给你看。

    ★ 税额用 含税 - 不含税 反推，不是 不含税 × 税率 直接算。
      两种算法在分位上会差一分：10000 含税、6% 税率，
      不含税 9433.96，× 6% = 566.04，而 10000 - 9433.96 = 566.04 —— 这次一样；
      但换个数就未必。**合计必须严格等于两个分项之和**，
      不然客户拿计算器一加，发现总数对不上分项，整张账单的可信度就没了。
    """
    v = Decimal(str(vat_rate or 0))
    if tax_included:
        gross = money(gross_or_net)
        net = money(gross / (Decimal("1") + v)) if v else gross
        tax = money(gross - net)
    else:
        net = money(gross_or_net)
        gross = money(net * (Decimal("1") + v))
        tax = money(gross - net)
    return net, tax, gross


# ================================================================ 账单


def _next_number(session: Session, tenant_id: int, today: date) -> str:
    """账单号：ZD-年月-序号。★ 全所唯一，开出去就是对外凭证。"""
    prefix = "ZD{0}{1:02d}".format(today.year, today.month)
    n = session.scalar(select(func.count()).select_from(Invoice).where(
        Invoice.tenant_id == tenant_id, Invoice.number.like(prefix + "%"))) or 0
    return "{0}-{1:04d}".format(prefix, n + 1)


def _recalc(session: Session, inv: Invoice) -> Invoice:
    """按明细重算账单合计。★ 只此一处算，别处一律读结果。"""
    lines = list(session.scalars(select(InvoiceLine).where(
        InvoiceLine.invoice_id == inv.id)).all())

    fee = sum((l.amount for l in lines if l.kind == "fee"), ZERO)
    disb = sum((l.amount for l in lines if l.kind == "disbursement"), ZERO)
    disc = sum((l.amount for l in lines if l.kind == "discount"), ZERO)

    inv.fee_amount = money(fee)
    inv.disbursement_amount = money(disb)
    inv.discount_amount = money(disc)

    # ★ 只有律师费计增值税；代垫费用是**替客户垫的钱**，原价转付不加价，
    #   把它一起计税等于凭空多收客户一笔
    taxable = money(fee - disc)
    net, tax, gross = split_tax(taxable, inv.vat_rate, tax_included=False)
    inv.net_amount = money(net + disb)
    inv.tax_amount = tax
    inv.total_amount = money(gross + disb)
    return inv


def create_invoice(session: Session, principal: Principal,
                   case_id: Optional[int] = None, client_id: Optional[int] = None,
                   period_from: Optional[date] = None,
                   period_to: Optional[date] = None,
                   due_on: Optional[date] = None, note: str = "",
                   today: Optional[date] = None) -> Invoice:
    if not principal.can("billing.manage"):
        raise DomainError("开账单需要 billing.manage 权限", code="billing.forbidden")
    _writable(session, principal, case_id)
    today = today or date.today()

    eng = engagement_of(session, principal, case_id) if case_id else None
    inv = Invoice(
        tenant_id=principal.tenant_id,
        number=_next_number(session, principal.tenant_id, today),
        case_id=case_id, client_id=client_id or (eng.client_id if eng else None),
        engagement_id=eng.id if eng else None,
        period_from=period_from, period_to=period_to, due_on=due_on,
        vat_rate=eng.vat_rate if eng else DEFAULT_VAT_RATE,
        note=note, status="draft", created_by=principal.user_id,
    )
    session.add(inv)
    session.flush()
    return inv


def _get_invoice(session: Session, principal: Principal, invoice_id: int) -> Invoice:
    inv = session.scalar(select(Invoice).where(
        Invoice.id == invoice_id, Invoice.tenant_id == principal.tenant_id))
    if inv is None:
        raise NotFound("账单不存在", code="billing.not_found")
    _visible(session, principal, inv.case_id)
    return inv


def _assert_draft(inv: Invoice) -> None:
    if inv.status in LOCKED_INVOICE_STATUSES:
        raise DomainError(
            "账单 {0} 已经是「{1}」，改不了。"
            "★ 改一张已经发出去的账单，等于两份文件在世上打架 —— "
            "要更正请红冲后重开。".format(
                inv.number, INVOICE_STATUS_LABELS.get(inv.status, inv.status)),
            code="billing.locked")


def add_line(session: Session, principal: Principal, invoice_id: int,
             description: str, amount, kind: str = "fee",
             user_id: Optional[int] = None, minutes: int = 0, rate=None,
             worked_on: Optional[date] = None,
             source_entry_ids: str = "") -> InvoiceLine:
    """手工加一行。代垫费用、折让、以及计时之外的收费都走这里。"""
    inv = _get_invoice(session, principal, invoice_id)
    _assert_draft(inv)
    if kind not in LINE_KINDS:
        raise DomainError("账单行类型只能是：{0}".format("、".join(LINE_KINDS)),
                          code="billing.bad_line_kind")
    if not (description or "").strip():
        # ★ 和咨询摘要一个道理：客户逐行看，说不清的那行第一个被砍
        raise DomainError("账单每一行都要写明是什么 —— 客户是逐行看的",
                          code="billing.line_description_required")

    amt = money(amount)
    if kind == "discount":
        # 折让统一存正数，在合计里减掉 —— 存负数迟早有人把符号搞反
        amt = abs(amt)
    if amt <= ZERO:
        raise DomainError("金额必须大于 0", code="billing.bad_amount")

    n = session.scalar(select(func.count()).select_from(InvoiceLine).where(
        InvoiceLine.invoice_id == invoice_id)) or 0
    row = InvoiceLine(
        tenant_id=principal.tenant_id, invoice_id=invoice_id, kind=kind,
        description=description.strip(), user_id=user_id, minutes=minutes,
        rate=money(rate) if rate is not None else None, amount=amt,
        worked_on=worked_on, source_entry_ids=source_entry_ids, sort_order=n,
    )
    session.add(row)
    session.flush()
    _recalc(session, inv)
    session.flush()
    return row


def remove_line(session: Session, principal: Principal, line_id: int) -> Invoice:
    row = session.scalar(select(InvoiceLine).where(
        InvoiceLine.id == line_id,
        InvoiceLine.tenant_id == principal.tenant_id))
    if row is None:
        raise NotFound("账单行不存在", code="billing.line_not_found")
    inv = _get_invoice(session, principal, row.invoice_id)
    _assert_draft(inv)
    session.delete(row)
    session.flush()
    _recalc(session, inv)
    session.flush()
    return inv


def lines_of(session: Session, principal: Principal,
             invoice_id: int) -> List[InvoiceLine]:
    _get_invoice(session, principal, invoice_id)
    return list(session.scalars(select(InvoiceLine).where(
        InvoiceLine.invoice_id == invoice_id
    ).order_by(InvoiceLine.sort_order, InvoiceLine.id)).all())


# ================================================================ 从咨询生成


def pull_consult(session: Session, principal: Principal, invoice_id: int,
                 since: Optional[date] = None, until: Optional[date] = None,
                 client_id: Optional[int] = None) -> Dict[str, Any]:
    """把还没出账的**咨询费**拉进账单。

    ★ 这比原来的 pull_time 简单得多，而且是**故意的**。

      拉工时要查每个人在那一天的费率、要按职级找价、要处理"这人没配费率"
      —— 那一整套是计时制律所的复杂度。
      咨询不需要：**金额在记录那一刻就算好并钉死了**（见 consult.log）。
      费率以后调价，去年那条咨询也不会跟着变 —— 这是记账的基本要求。

    ★ 哪些咨询能进账单，**由 consult 自己判**（免费的、已出账的、
      作废的都不能）。billing 不该知道那些状态是怎么回事。
    """
    inv = _get_invoice(session, principal, invoice_id)
    _assert_draft(inv)

    api = _consult_api()
    if api is None:
        raise DomainError("没有装 consult 模块，开不了咨询费账单",
                          code="billing.consult_module_missing")

    # 账单挂了案件就按案件捞；否则要指明客户
    cid = client_id
    if not cid:
        if not inv.case_id:
            raise DomainError(
                "这张账单既没挂案件、也没指定客户，不知道该拉谁的咨询",
                code="billing.consult_target_missing")
        case = _case_api().get_case(session, inv.case_id)
        cid = getattr(case, "client_id", None)
        if not cid:
            raise DomainError("这个案件没挂客户，拉不了咨询",
                              code="billing.case_without_client")

    rows = api.billable(session, principal, cid,
                        case_id=inv.case_id, since=since, until=until)
    if not rows:
        return {"pulled": 0, "minutes": 0, "amount": str(ZERO), "lines": 0}

    identity = registry.api("identity")
    made = 0
    total = ZERO
    for r in rows:
        d = api.describe(r)
        try:
            who = identity.get_user(session, d["user_id"]).full_name
        except Exception:                                # noqa: BLE001
            who = "#{0}".format(d["user_id"])
        hours = Decimal(d["billable_minutes"]) / Decimal(60)
        amt = money(d["amount"])
        # ★ 摘要直接用咨询记录里那句话 —— 它本来就是写给客户读的
        label = "{0}　法律咨询 {1} 小时　{2}".format(
            who, hours.quantize(Decimal("0.1")), d["channel_label"])
        if d["summary"]:
            label += "　" + d["summary"].splitlines()[0][:40]
        add_line(session, principal, invoice_id, label, amt,
                 kind="fee", user_id=d["user_id"],
                 minutes=d["billable_minutes"],
                 rate=money(d["hourly_rate"]),
                 worked_on=date.fromisoformat(d["happened_on"]),
                 source_entry_ids="C{0}".format(d["id"]))
        made += 1
        total += amt

    session.flush()
    _recalc(session, inv)
    session.flush()
    _check_cap(session, principal, inv)
    return {
        "pulled": len(rows), "lines": made,
        "minutes": sum(api.describe(r)["billable_minutes"] for r in rows),
        "amount": str(total),
    }


def _check_cap(session: Session, principal: Principal, inv: Invoice) -> None:
    """封顶价检查。★ 只提示不硬拦 —— 超没超要看整个案子累计，不是这一张。"""
    if not inv.engagement_id:
        return
    eng = session.get(Engagement, inv.engagement_id)
    if eng is None or eng.cap_amount is None:
        return
    billed = session.scalar(select(func.coalesce(func.sum(Invoice.fee_amount), 0)).where(
        Invoice.tenant_id == inv.tenant_id,
        Invoice.case_id == inv.case_id,
        Invoice.status.notin_(["void"]),
    )) or ZERO
    if money(billed) > eng.cap_amount:
        logger.warning("案件 %s 累计律师费 %s 超过封顶 %s",
                       inv.case_id, billed, eng.cap_amount)


def bill_fixed(session: Session, principal: Principal, invoice_id: int,
               amount=None, description: str = "") -> InvoiceLine:
    """按固定收费约定开一行。"""
    inv = _get_invoice(session, principal, invoice_id)
    eng = session.get(Engagement, inv.engagement_id) if inv.engagement_id else None
    if eng is None:
        raise DomainError("这个案子还没有收费约定", code="billing.no_engagement")
    if eng.mode not in ("fixed", "retainer", "hybrid"):
        raise DomainError("当前收费模式是「{0}」，不该走固定收费".format(
            FEE_MODE_LABELS.get(eng.mode, eng.mode)), code="billing.mode_mismatch")

    amt = money(amount) if amount is not None else (
        eng.base_amount if eng.mode == "hybrid" else eng.fixed_amount)
    # ★ 约定含税时，账单行存的是**不含税**金额 —— 行是净额，税在合计里加。
    #   不换算的话，含税报价会被再计一次税，客户凭空多付 6%
    if eng.tax_included:
        amt, _, _ = split_tax(amt, eng.vat_rate, tax_included=True)
    return add_line(session, principal, invoice_id,
                    description or FEE_MODE_LABELS.get(eng.mode, "律师费"),
                    amt, kind="fee")


def bill_contingency(session: Session, principal: Principal, invoice_id: int,
                     recovered=None, description: str = "") -> InvoiceLine:
    """按风险代理约定开一行。

    ★ 这里**再查一次 30% 上限**，虽然签约时已经查过。
      理由：约定可能被人改过，标的额也可能变（调解结案金额降了）。
      一道法定红线，宁可查两遍。
    """
    inv = _get_invoice(session, principal, invoice_id)
    eng = session.get(Engagement, inv.engagement_id) if inv.engagement_id else None
    if eng is None:
        raise DomainError("这个案子还没有收费约定", code="billing.no_engagement")
    if eng.mode not in ("contingency", "hybrid"):
        raise DomainError("当前收费模式是「{0}」，不该走风险收费".format(
            FEE_MODE_LABELS.get(eng.mode, eng.mode)), code="billing.mode_mismatch")

    base = money(recovered) if recovered is not None else (
        eng.contingency_base or ZERO)
    if base <= ZERO:
        raise DomainError(
            "风险代理要按实际回款或约定标的额算，这个数不能是 0 —— "
            "请填写本案实际收回的金额。",
            code="billing.contingency_base_required")

    if eng.contingency_rate > legal.CONTINGENCY_MAX_RATE:
        raise DomainError(
            "约定的风险比例 {0} 超过法定上限 {1}（{2}）。这张账单不能开。".format(
                eng.contingency_rate, legal.CONTINGENCY_MAX_RATE,
                legal.BASIS_ARTICLE_13),
            code="billing.contingency_forbidden")

    amt = money(base * eng.contingency_rate)
    if eng.tax_included:
        amt, _, _ = split_tax(amt, eng.vat_rate, tax_included=True)
    return add_line(
        session, principal, invoice_id,
        description or "风险代理费：按实收 {0} 元的 {1}% 计".format(
            base, (eng.contingency_rate * 100).quantize(CENT)),
        amt, kind="fee")


# ================================================================ 代垫费用


def record_disbursement(session: Session, principal: Principal, case_id: int,
                        amount, description: str, spent_on: Optional[date] = None,
                        ticket_id: Optional[int] = None,
                        paid_by: Optional[int] = None,
                        note: str = "") -> Disbursement:
    """记一笔已经垫出去、还没开进账单的代垫费用。

    ★ 主要给 approval（Step 18）用：请款单走完流程、钱付出去了，
      这笔代垫就自动挂在案子上等着开票。

      不这么做的话，垫钱和开账单差着好几个月 —— 律师三月替客户垫了
      鉴定费，六月开账单时全靠翻财务凭证回忆。**翻漏了就是律所自己贴钱**，
      而且和漏记工时一样，没人会发现。
    """
    _writable(session, principal, case_id)
    amt = money(amount)
    if amt <= ZERO:
        raise DomainError("代垫金额必须大于 0", code="billing.bad_amount")
    if not (description or "").strip():
        # 这句话会原样进账单行，客户要看得懂
        raise DomainError("代垫费用要写明是什么 —— 这句话会原样进账单",
                          code="billing.line_description_required")
    row = Disbursement(
        tenant_id=principal.tenant_id, case_id=case_id, amount=amt,
        description=description.strip()[:255], spent_on=spent_on or date.today(),
        ticket_id=ticket_id, paid_by=paid_by, note=note[:255],
    )
    session.add(row)
    session.flush()
    return row


def pending_disbursements(session: Session, principal: Principal,
                          case_id: int) -> List[Disbursement]:
    """这个案子上垫了、还没开票的代垫费用。"""
    _visible(session, principal, case_id)
    return list(session.scalars(select(Disbursement).where(
        Disbursement.tenant_id == principal.tenant_id,
        Disbursement.case_id == case_id,
        Disbursement.billed.is_(False),
    ).order_by(Disbursement.spent_on, Disbursement.id)).all())


def pull_disbursements(session: Session, principal: Principal,
                       invoice_id: int) -> Dict[str, Any]:
    """把还没开票的代垫费用拉进账单。

    ★ 和 pull_consult 一样，标记放在 **issue** 那一步，不在这里 ——
      草稿还会加加减减，这时候就标成已开票，用户删掉一行之后
      那笔代垫就再也回不来了。
    """
    inv = _get_invoice(session, principal, invoice_id)
    _assert_draft(inv)
    if not inv.case_id:
        raise DomainError("这张账单没挂案件，拉不了代垫费用",
                          code="billing.invoice_without_case")

    rows = pending_disbursements(session, principal, inv.case_id)
    total = ZERO
    for d in rows:
        add_line(session, principal, invoice_id,
                 "{0}（{1} 垫付）".format(d.description, d.spent_on),
                 d.amount, kind="disbursement", worked_on=d.spent_on,
                 source_entry_ids="D{0}".format(d.id))
        total += d.amount
    session.flush()
    return {"pulled": len(rows), "amount": str(money(total))}


# ================================================================ 开出与回款


def issue(session: Session, principal: Principal, invoice_id: int,
          issued_on: Optional[date] = None,
          due_on: Optional[date] = None) -> Invoice:
    """开出账单。**这一步之后就锁死了。**

    同时把关联的工时推成 billed —— 通过 time 的 api，不碰它的表。
    """
    if not principal.can("billing.issue"):
        raise DomainError("开出账单需要 billing.issue 权限", code="billing.forbidden")
    inv = _get_invoice(session, principal, invoice_id)
    _assert_draft(inv)

    lines = lines_of(session, principal, invoice_id)
    if not lines:
        raise DomainError("空账单开不出去", code="billing.empty_invoice")
    _recalc(session, inv)
    if inv.total_amount <= ZERO:
        raise DomainError("账单合计是 {0}，开不出去 —— 先看看是不是折让填反了".format(
            inv.total_amount), code="billing.bad_total")

    # ★ 把咨询推成已出账。放在 issue 而不是 pull_consult：
    #   草稿阶段还会加加减减，那时候就锁上，等于用户删掉一行之后
    #   那条咨询再也回不来了
    #
    # ★ source_entry_ids 里混着几种来源，**靠前缀区分**：
    #       "C123"  咨询
    #       "D123"  代垫费用
    #       纯数字   历史遗留（原来的工时，模块已拆）
    #   前缀是刻意的（一眼看得出这行是哪来的），但读的时候就得认它。
    #   第一版无差别 int() 一遍，一张带代垫的账单直接 ValueError 开不出去 ——
    #   所以这里只挑自己认识的前缀，**不认识的一律跳过**，
    #   绝不假设"剩下的都是我的"。
    ids: List[int] = []
    for l in lines:
        if not l.source_entry_ids:
            continue
        for x in l.source_entry_ids.split(","):
            x = x.strip()
            if x.startswith("C") and x[1:].isdigit():
                ids.append(int(x[1:]))
    if ids:
        api = _consult_api()
        if api is not None:
            api.mark_billed(session, principal, ids, inv.number)

    # 代垫费用同理：开出这一步才标已开票，草稿阶段不动
    disb_ids = []
    for l in lines:
        if l.source_entry_ids.startswith("D"):
            try:
                disb_ids.append(int(l.source_entry_ids[1:]))
            except ValueError:
                pass
    for d in session.scalars(select(Disbursement).where(
            Disbursement.id.in_(disb_ids),
            Disbursement.tenant_id == principal.tenant_id)).all() if disb_ids else []:
        d.billed = True
        d.invoice_id = inv.id

    inv.status = "issued"
    inv.issued_on = issued_on or date.today()
    inv.due_on = due_on or inv.due_on
    inv.issued_by = principal.user_id
    session.flush()
    return inv


def record_payment(session: Session, principal: Principal, invoice_id: int,
                   amount, paid_on: Optional[date] = None,
                   method: str = "transfer", reference: str = "",
                   note: str = "", today: Optional[date] = None) -> Payment:
    """登记一笔回款。

    ★ 开了账单不等于收到钱。律所真正的风险不在"没开单"，
      在"开了单收不回来" —— 所以回款必须是独立一步，不能开单时顺手勾"已收"。
    """
    if not principal.can("billing.payment"):
        raise DomainError("登记回款需要 billing.payment 权限",
                          code="billing.forbidden")
    today = today or date.today()
    inv = _get_invoice(session, principal, invoice_id)
    if inv.status == "draft":
        raise DomainError("账单还没开出去，收不了款 —— 先开出账单",
                          code="billing.not_issued")
    if inv.status == "void":
        raise DomainError("这张账单已经红冲，收不了款", code="billing.voided")
    if method not in PAY_METHODS:
        raise DomainError("回款方式只能是：{0}".format("、".join(PAY_METHODS)),
                          code="billing.bad_method")

    amt = money(amount)
    if amt <= ZERO:
        raise DomainError("回款金额必须大于 0", code="billing.bad_amount")
    if paid_on and paid_on > today:
        raise DomainError("不能登记未来的回款（{0}）".format(paid_on),
                          code="billing.future_payment")

    outstanding = money(inv.total_amount - inv.paid_amount)
    if amt > outstanding:
        # ★ 多收的钱不能悄悄入账。可能是客户付错了、可能是合并付款，
        #   两种都得人来判断 —— 系统替他决定，就是替他扛责任
        raise DomainError(
            "这笔 {0} 元超过账单未收余额 {1} 元。"
            "多收的部分请单独处理（客户付错？合并付款？）—— "
            "系统不替你决定这笔钱算哪张单的。".format(amt, outstanding),
            code="billing.overpay")

    row = Payment(
        tenant_id=principal.tenant_id, invoice_id=invoice_id, amount=amt,
        paid_on=paid_on or today, method=method, reference=reference,
        note=note, recorded_by=principal.user_id,
    )
    session.add(row)
    session.flush()
    _apply_payments(session, inv)
    session.flush()
    return row


def reverse_payment(session: Session, principal: Principal, payment_id: int,
                    reason: str) -> Payment:
    """冲回一笔回款（退款、记错了）。★ 不删除 —— 钱的记录不能凭空消失。"""
    if not reason.strip():
        raise DomainError("冲回必须写明原因", code="billing.reverse_reason_required")
    row = session.scalar(select(Payment).where(
        Payment.id == payment_id, Payment.tenant_id == principal.tenant_id))
    if row is None:
        raise NotFound("回款记录不存在", code="billing.payment_not_found")
    if row.reversed:
        raise DomainError("这笔回款已经冲回过了", code="billing.already_reversed")
    inv = _get_invoice(session, principal, row.invoice_id)
    row.reversed = True
    row.reverse_reason = reason.strip()[:255]
    session.flush()
    _apply_payments(session, inv)
    session.flush()
    return row


def _apply_payments(session: Session, inv: Invoice) -> Invoice:
    paid = session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.invoice_id == inv.id, Payment.reversed.is_(False))) or ZERO
    inv.paid_amount = money(paid)
    if inv.status != "void":
        if inv.paid_amount >= inv.total_amount:
            inv.status = "paid"
        elif inv.paid_amount > ZERO:
            inv.status = "partial"
        else:
            inv.status = "issued"
    return inv


def void_invoice(session: Session, principal: Principal, invoice_id: int,
                 reason: str) -> Invoice:
    """红冲。★ 不是删除 —— 一张凭空消失的账单比一张错的更可疑。"""
    if not principal.can("billing.issue"):
        raise DomainError("红冲需要 billing.issue 权限", code="billing.forbidden")
    if not reason.strip():
        raise DomainError("红冲必须写明原因", code="billing.void_reason_required")
    inv = _get_invoice(session, principal, invoice_id)
    if inv.status == "void":
        raise DomainError("这张账单已经红冲过了", code="billing.already_void")
    if inv.paid_amount > ZERO:
        raise DomainError(
            "这张账单已经收了 {0} 元，不能直接红冲 —— "
            "请先把回款冲回，再红冲账单。".format(inv.paid_amount),
            code="billing.has_payment")
    inv.status = "void"
    inv.void_reason = reason.strip()[:255]
    session.flush()
    return inv


# ================================================================ 查询


def invoices_of_case(session: Session, principal: Principal,
                     case_id: int) -> List[Invoice]:
    _visible(session, principal, case_id)
    return list(session.scalars(select(Invoice).where(
        Invoice.tenant_id == principal.tenant_id, Invoice.case_id == case_id
    ).order_by(Invoice.id.desc())).all())


def payments_of(session: Session, principal: Principal,
                invoice_id: int) -> List[Payment]:
    _get_invoice(session, principal, invoice_id)
    return list(session.scalars(select(Payment).where(
        Payment.invoice_id == invoice_id).order_by(Payment.paid_on, Payment.id)).all())


#: 账龄分档（天）
AGING_BUCKETS = ((0, 30), (31, 60), (61, 90), (91, 180), (181, 99999))
AGING_LABELS = ("30 天内", "31–60 天", "61–90 天", "91–180 天", "180 天以上")


def receivables(session: Session, principal: Principal,
                today: Optional[date] = None) -> Dict[str, Any]:
    """应收账款与账龄。

    ★ 律所真正的风险不是"没开单"，是"**开了单收不回来**"。
      所以这个视图的主角是账龄，不是总额：
      一笔挂了 200 天的应收，和一笔上周开的同额账单，是两回事。
    """
    if not principal.can("billing.view"):
        raise DomainError("查看账款需要 billing.view 权限", code="billing.forbidden")
    today = today or date.today()
    rows = list(session.scalars(select(Invoice).where(
        Invoice.tenant_id == principal.tenant_id,
        Invoice.status.in_(["issued", "partial"]),
    )).all())
    rows = [r for r in rows if _can_see(session, principal, r.case_id)]

    buckets = [{"label": AGING_LABELS[i], "count": 0, "amount": ZERO}
               for i in range(len(AGING_BUCKETS))]
    total = ZERO
    overdue = ZERO
    items = []
    for r in rows:
        out = money(r.total_amount - r.paid_amount)
        if out <= ZERO:
            continue
        base = r.issued_on or today
        age = (today - base).days
        for i, (lo, hi) in enumerate(AGING_BUCKETS):
            if lo <= age <= hi:
                buckets[i]["count"] += 1
                buckets[i]["amount"] += out
                break
        total += out
        if r.due_on and r.due_on < today:
            overdue += out
        items.append({
            "id": r.id, "number": r.number, "case_id": r.case_id,
            "issued_on": r.issued_on.isoformat() if r.issued_on else "",
            "due_on": r.due_on.isoformat() if r.due_on else "",
            "total": str(r.total_amount), "paid": str(r.paid_amount),
            "outstanding": str(out), "age_days": age,
            "overdue": bool(r.due_on and r.due_on < today),
        })
    items.sort(key=lambda x: -x["age_days"])
    return {
        "total_outstanding": str(money(total)),
        "overdue_amount": str(money(overdue)),
        "invoice_count": len(items),
        "aging": [{"label": b["label"], "count": b["count"],
                   "amount": str(money(b["amount"]))} for b in buckets],
        "items": items,
    }


def _can_see(session: Session, principal: Principal,
             case_id: Optional[int]) -> bool:
    if not case_id:
        return True
    try:
        _case_api().assert_visible(session, principal, case_id)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def case_billing(session: Session, principal: Principal,
                 case_id: int) -> Dict[str, Any]:
    """一个案子的收费全貌：约定、已开、已收、未收。"""
    _visible(session, principal, case_id)
    eng = engagement_of(session, principal, case_id)
    invs = [i for i in invoices_of_case(session, principal, case_id)
            if i.status != "void"]
    billed = sum((i.total_amount for i in invs), ZERO)
    paid = sum((i.paid_amount for i in invs), ZERO)
    return {
        "case_id": case_id,
        "engagement": describe_engagement(eng) if eng else None,
        "invoice_count": len(invs),
        "billed": str(money(billed)),
        "paid": str(money(paid)),
        "outstanding": str(money(billed - paid)),
        "draft_count": len([i for i in invs if i.status == "draft"]),
    }


# ================================================================ 展示


def describe_engagement(e: Engagement) -> Dict[str, Any]:
    return {
        "id": e.id, "case_id": e.case_id, "client_id": e.client_id,
        "mode": e.mode, "mode_label": FEE_MODE_LABELS.get(e.mode, e.mode),
        "fixed_amount": str(e.fixed_amount) if e.fixed_amount is not None else "",
        "contingency_rate": str(e.contingency_rate) if e.contingency_rate is not None else "",
        "contingency_pct": (str((e.contingency_rate * 100).quantize(CENT))
                            if e.contingency_rate is not None else ""),
        "contingency_base": str(e.contingency_base) if e.contingency_base is not None else "",
        "base_amount": str(e.base_amount) if e.base_amount is not None else "",
        "cap_amount": str(e.cap_amount) if e.cap_amount is not None else "",
        "consult_rate": str(e.consult_rate) if e.consult_rate is not None else "",
        "tax_included": e.tax_included,
        "vat_rate": str(e.vat_rate),
        "vat_pct": str((e.vat_rate * 100).quantize(CENT)),
        "currency": e.currency,
        "contract_no": e.contract_no,
        "signed_on": e.signed_on.isoformat() if e.signed_on else "",
        #: ★ 签约时的合法性提示原样留着 —— 事后能证明提示过
        "legality_note": e.legality_note,
        "confirmed": bool(e.confirmed_at),
        "note": e.note, "active": e.active,
    }


def describe_invoice(i: Invoice) -> Dict[str, Any]:
    return {
        "id": i.id, "number": i.number, "case_id": i.case_id,
        "client_id": i.client_id, "engagement_id": i.engagement_id,
        "period_from": i.period_from.isoformat() if i.period_from else "",
        "period_to": i.period_to.isoformat() if i.period_to else "",
        "issued_on": i.issued_on.isoformat() if i.issued_on else "",
        "due_on": i.due_on.isoformat() if i.due_on else "",
        "fee_amount": str(i.fee_amount),
        "disbursement_amount": str(i.disbursement_amount),
        "discount_amount": str(i.discount_amount),
        "net_amount": str(i.net_amount),
        "tax_amount": str(i.tax_amount),
        "total_amount": str(i.total_amount),
        "paid_amount": str(i.paid_amount),
        "outstanding": str(money(i.total_amount - i.paid_amount)),
        "vat_rate": str(i.vat_rate),
        "vat_pct": str((i.vat_rate * 100).quantize(CENT)),
        "currency": i.currency,
        "status": i.status,
        "status_label": INVOICE_STATUS_LABELS.get(i.status, i.status),
        "locked": i.status in LOCKED_INVOICE_STATUSES,
        "void_reason": i.void_reason,
        "note": i.note,
    }


def describe_line(l: InvoiceLine) -> Dict[str, Any]:
    from modules.billing.models import LINE_KIND_LABELS
    return {
        "id": l.id, "invoice_id": l.invoice_id, "kind": l.kind,
        "kind_label": LINE_KIND_LABELS.get(l.kind, l.kind),
        "description": l.description, "user_id": l.user_id,
        "minutes": l.minutes,
        "hours": str((Decimal(l.minutes) / 60).quantize(Decimal("0.1"))) if l.minutes else "",
        "rate": str(l.rate) if l.rate is not None else "",
        "amount": str(l.amount),
        "worked_on": l.worked_on.isoformat() if l.worked_on else "",
        "source_entry_ids": l.source_entry_ids,
    }


def describe_disbursement(d: Disbursement) -> Dict[str, Any]:
    return {
        "id": d.id, "case_id": d.case_id, "amount": str(d.amount),
        "description": d.description, "spent_on": d.spent_on.isoformat(),
        "ticket_id": d.ticket_id, "paid_by": d.paid_by,
        "billed": d.billed, "invoice_id": d.invoice_id, "note": d.note,
    }


def describe_payment(p: Payment) -> Dict[str, Any]:
    from modules.billing.models import PAY_METHOD_LABELS
    return {
        "id": p.id, "invoice_id": p.invoice_id, "amount": str(p.amount),
        "paid_on": p.paid_on.isoformat(), "method": p.method,
        "method_label": PAY_METHOD_LABELS.get(p.method, p.method),
        "reference": p.reference, "note": p.note,
        "reversed": p.reversed, "reverse_reason": p.reverse_reason,
    }


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的账单与回款。"""
    _visible(session, principal, case_id)
    invs = [i for i in invoices_of_case(session, principal, case_id)
            if i.status != "void"]
    out = []
    if invs:
        total = sum((i.total_amount for i in invs), ZERO)
        out.append({"name": "账单", "count": len(invs),
                    "note": "合计 {0} 元".format(money(total))})
        n = session.scalar(select(func.count()).select_from(Payment).where(
            Payment.tenant_id == principal.tenant_id,
            Payment.invoice_id.in_([i.id for i in invs]),
            Payment.reversed.is_(False))) or 0
        if n:
            out.append({"name": "回款记录", "count": int(n), "note": ""})
    return out


def archive_blockers(session, principal, case_id):
    """★ 钱没清完不能归档 —— 这是归档前最要紧的一条检查。"""
    _visible(session, principal, case_id)
    out = []
    invs = invoices_of_case(session, principal, case_id)
    drafts = [i for i in invs if i.status == "draft"]
    if drafts:
        out.append({"what": "还有 {0} 张草稿账单没开出".format(len(drafts)),
                    "how": "开出或删掉 —— 归档后开不了了"})
    unpaid = [i for i in invs if i.status in ("issued", "partial")]
    if unpaid:
        owed = sum((money(i.total_amount - i.paid_amount) for i in unpaid), ZERO)
        out.append({"what": "还有 {0} 元没收回来（{1} 张单）".format(
                        money(owed), len(unpaid)),
                    "how": "收齐，或者确认是坏账后强制归档"})
    pend = pending_disbursements(session, principal, case_id)
    if pend:
        out.append({"what": "有 {0} 笔代垫还没开进账单".format(len(pend)),
                    "how": "拉进账单开出去 —— 不然这笔垫出去的钱收不回来了"})
    return out


# ================================================================ 看板指标


def _revenue_rows(session, principal, spec):
    """本期口径内的账单。★ 用户可见范围已经在 invoices 那边过滤过。"""
    stmt = select(Invoice).where(
        Invoice.tenant_id == principal.tenant_id,
        Invoice.status != "void",
    )
    rows = [i for i in session.scalars(stmt).all()
            if _can_see(session, principal, i.case_id)]

    case_api = _case_api()
    keep = []
    for i in rows:
        if spec.get("user_id") or spec.get("org_id"):
            if not i.case_id:
                continue
            try:
                c = case_api.get_case(session, i.case_id)
            except Exception:                            # noqa: BLE001
                continue
            if spec.get("user_id") and c.lead_id != spec["user_id"]:
                continue
            if spec.get("org_id") and c.org_unit_id != spec["org_id"]:
                continue
        keep.append(i)
    return keep


def metrics(session, principal, spec=None):
    """★ 创收。**三个口径必须分清，混了就有人拿开票额去邀功。**

        已开票   开了账单 —— 不代表收到
        已回款   钱到账了 —— **律所说"创收"通常指这个**
        未收     开了收不回来的

    ★ 归到人是按**主办律师**（case.lead_id）。案源分成还没做，
      所以界面上要写明这是"主办口径"，别让案源律师以为系统漏算了他。
    """
    spec = spec or {}
    since = spec.get("since")
    until = spec.get("until")
    today = spec.get("today") or date.today()

    rows = _revenue_rows(session, principal, spec)

    def _issued_in(i):
        d = i.issued_on
        if d is None:
            return False
        return (not since or d >= since) and (not until or d <= until)

    period = [i for i in rows if _issued_in(i)]
    billed = sum((i.total_amount for i in period), ZERO)

    # 回款按**收款日**算，不按账单日 —— 三月开的单五月收到，是五月的创收
    pay_rows = list(session.scalars(select(Payment).where(
        Payment.tenant_id == principal.tenant_id,
        Payment.reversed.is_(False),
        Payment.invoice_id.in_([i.id for i in rows]) if rows else False,
    )).all()) if rows else []
    if since:
        pay_rows = [p for p in pay_rows if p.paid_on >= since]
    if until:
        pay_rows = [p for p in pay_rows if p.paid_on <= until]
    received = sum((p.amount for p in pay_rows), ZERO)

    live = [i for i in rows if i.status in ("issued", "partial")]
    outstanding = sum((money(i.total_amount - i.paid_amount) for i in live), ZERO)
    overdue = sum((money(i.total_amount - i.paid_amount) for i in live
                   if i.due_on and i.due_on < today), ZERO)

    return {
        "billing.billed": {"value": str(money(billed)),
                           "text": "¥%s" % money(billed)},
        "billing.received": {"value": str(money(received)),
                             "text": "¥%s" % money(received)},
        "billing.outstanding": {"value": str(money(outstanding)),
                                "text": "¥%s" % money(outstanding)},
        "billing.overdue": {"value": str(money(overdue)),
                            "text": "¥%s" % money(overdue)},
    }


def revenue_by_lead(session, principal, spec=None):
    """★ 按主办律师看创收 —— 团队页和全所页都用它。

    ★ **小样本保护**：分组人数少于 3 时只给总数不给分组。
      团队 3 个人、总额已知，知道另外两人就能反推第三个 ——
      聚合数字看着无害，实际是能反推到个人的。
    """
    spec = spec or {}
    rows = _revenue_rows(session, principal, spec)
    case_api = _case_api()

    by_lead = {}
    for i in rows:
        if not i.case_id:
            continue
        try:
            c = case_api.get_case(session, i.case_id)
        except Exception:                                # noqa: BLE001
            continue
        b = by_lead.setdefault(c.lead_id, {"billed": ZERO, "received": ZERO,
                                           "outstanding": ZERO, "cases": set()})
        b["billed"] += i.total_amount
        b["received"] += i.paid_amount
        if i.status in ("issued", "partial"):
            b["outstanding"] += money(i.total_amount - i.paid_amount)
        b["cases"].add(i.case_id)

    if 0 < len(by_lead) < 3:
        return {"suppressed": True, "reason":
                "只有 %d 个人有创收记录，拆开会反推到个人 —— 只给总数。"
                % len(by_lead),
                "rows": [], "total": {
                    "billed": str(money(sum((v["billed"] for v in by_lead.values()), ZERO))),
                    "received": str(money(sum((v["received"] for v in by_lead.values()), ZERO))),
                }}

    return {"suppressed": False, "reason": "", "rows": [
        {"user_id": uid,
         "billed": str(money(v["billed"])),
         "received": str(money(v["received"])),
         "outstanding": str(money(v["outstanding"])),
         "cases": len(v["cases"])}
        for uid, v in sorted(by_lead.items(),
                             key=lambda kv: -kv[1]["received"])
    ]}


def case_revenue(session, principal, spec=None):
    """按案件看创收 —— 找"开了收不回来"的那几个。"""
    spec = spec or {}
    rows = _revenue_rows(session, principal, spec)
    by_case = {}
    for i in rows:
        if not i.case_id:
            continue
        b = by_case.setdefault(i.case_id, {"billed": ZERO, "received": ZERO,
                                           "outstanding": ZERO})
        b["billed"] += i.total_amount
        b["received"] += i.paid_amount
        if i.status in ("issued", "partial"):
            b["outstanding"] += money(i.total_amount - i.paid_amount)
    out = [{"case_id": cid,
            "billed": str(money(v["billed"])),
            "received": str(money(v["received"])),
            "outstanding": str(money(v["outstanding"]))}
           for cid, v in by_case.items()]
    out.sort(key=lambda x: -float(x["outstanding"]))
    return out


def revenue_flow(session, principal: Principal,
                 spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """本期的**流水明细** —— 看板上「本期开票 / 本期回款」点开看到的东西。

    ★ 为什么单独有这个函数、而不是让前端去 receivables 里挑：
      应收页是**存量**页（截至今天还欠多少），
      开票额和回款额是**流量**（本期发生了多少）。
      把「本期回款 ¥206,000」点到一张讲未收的表上，
      两个数字八竿子打不着 —— 用户只会认为统计错了。

    ★ 这里刻意**复用 metrics 用的同一段筛选**（`_revenue_rows` + 同样的
      日期判断）。口径各写一遍迟早会漂：
      **格子里的数必须正好等于点开之后这些行加起来的数。**
    """
    spec = dict(spec or {})
    since = spec.get("since")
    until = spec.get("until")

    rows = _revenue_rows(session, principal, spec)

    def _in(d):
        if d is None:
            return False
        return (not since or d >= since) and (not until or d <= until)

    issued = [i for i in rows if _in(i.issued_on)]
    issued.sort(key=lambda i: (i.issued_on or date.min), reverse=True)

    pays = []
    if rows:
        by_inv = {i.id: i for i in rows}
        for p in session.scalars(select(Payment).where(
            Payment.tenant_id == principal.tenant_id,
            Payment.reversed.is_(False),
            Payment.invoice_id.in_(list(by_inv)),
        )).all():
            if not _in(p.paid_on):
                continue
            inv = by_inv[p.invoice_id]
            pays.append({
                "id": p.id, "paid_on": p.paid_on.isoformat(),
                "amount": str(money(p.amount)),
                "method": getattr(p, "method", "") or "",
                "invoice_id": inv.id, "number": inv.number,
                "case_id": inv.case_id,
            })
    pays.sort(key=lambda x: x["paid_on"], reverse=True)

    return {
        "since": since.isoformat() if since else "",
        "until": until.isoformat() if until else "",
        "billed": [{
            "id": i.id, "number": i.number, "case_id": i.case_id,
            "issued_on": i.issued_on.isoformat() if i.issued_on else "",
            "amount": str(money(i.total_amount)),
            "paid": str(money(i.paid_amount)),
            "status": i.status,
        } for i in issued],
        "billed_total": str(money(sum((i.total_amount for i in issued), ZERO))),
        "received": pays,
        "received_total": str(money(sum(
            (money(p["amount"]) for p in pays), ZERO))),
    }
