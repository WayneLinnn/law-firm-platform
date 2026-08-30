# -*- coding: utf-8 -*-
"""法律咨询：记一次咨询，算出钱，能进账单。

★ **这个模块刻意做得很小。** 它是 time（工时）拆掉之后剩下的那一点。

  原来那套是英美计时制律所的做法：人人每天记满 8 小时、按计费率换算、
  逐级审批、月底出账。中国的提成制律所不这么运转 ——
  团队成员和律师助理是工资制或分成制，他们的时间不换算成客户账单。
  硬套下去的结果是所有人应付式补录，
  **而假数据比没数据更糟：它会被拿去算成本、算人效、算分成。**

  真正按时间收钱的只有一件事：法律咨询。所以只留这一件。

★ 没有审批流。一次咨询就是一笔已经发生的收费事实，
  不像工时那样需要"这 40 小时该不该报给客户"的判断。
  加一道审批只会让人拖着不记。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.consult.models import (
    CHANNEL_LABELS,
    CHANNELS,
    LOCKED_STATUSES,
    STATUSES,
    Consult,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")

STATUS_LABELS = {"draft": "待出账", "billed": "已出账", "void": "已作废"}

#: 计费的最小单位：半小时。
#:
#: ★ 咨询按**半小时**取整，不像工时那样按 6 分钟。
#:   6 分钟制是计时律所为了把每一分钟都换成钱设计的；
#:   咨询是"聊了一个多小时"这种粒度，
#:   报一个 1.3 小时的数字只会让客户觉得你在抠。
BILLING_INCREMENT_MINUTES = 30

#: 低于这个时长不收费。见面寒暄二十分钟就开账单，客户不会有第二次。
MIN_BILLABLE_MINUTES = 15


def money(x) -> Decimal:
    """钱一律两位，**四舍五入**（不是银行家舍入）。

    ★ 中国的发票和结算用的就是四舍五入，
      Python 默认的 ROUND_HALF_EVEN 会把 0.125 舍成 0.12，和财务对不上。
    """
    return Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _crm():
    return registry.api("crm")


def _case_api():
    return registry.optional_api("case")


# ---------------------------------------------------------------- 费率


def rate_for(session: Session, principal: Principal,
             user_id: Optional[int] = None) -> Decimal:
    """这个人的咨询费率。

    ★ 复用 billing 的费率表（`billing.rate_for`），**不另建一套**。
      两套费率表最后一定会打架：财务在一处调了价，另一处还是老数，
      然后同一个律师同一天的两条记录单价不一样。
    """
    api = registry.optional_api("billing")
    if api is None:
        return ZERO
    try:
        return money(api.rate_for(session, principal.tenant_id,
                                  user_id or principal.user_id))
    except Exception as exc:                             # noqa: BLE001
        logger.warning("取咨询费率失败，按 0 处理：%s", exc)
        return ZERO


def billable_minutes(minutes: int) -> int:
    """按半小时向上取整。不足 15 分钟不计费。"""
    if minutes < MIN_BILLABLE_MINUTES:
        return 0
    inc = BILLING_INCREMENT_MINUTES
    return ((minutes + inc - 1) // inc) * inc


# ---------------------------------------------------------------- 权限


def _can_see(principal: Principal, row: Consult) -> bool:
    if principal.can("consult.view_all"):
        return True
    return row.user_id == principal.user_id


def _get(session: Session, principal: Principal, consult_id: int) -> Consult:
    row = session.get(Consult, consult_id)
    if row is None or row.tenant_id != principal.tenant_id:
        raise NotFound("咨询记录不存在")
    if not _can_see(principal, row):
        raise DomainError("你只能看自己的咨询记录", code="consult.forbidden")
    return row


def _assert_editable(row: Consult) -> None:
    if row.status in LOCKED_STATUSES:
        raise DomainError(
            "这条咨询已经{0}，改不了了。\n"
            "已经报给客户的数字不能事后悄悄改 —— 要改只能作废重记。".format(
                STATUS_LABELS.get(row.status, row.status)),
            code="consult.locked",
        )


# ---------------------------------------------------------------- 记录


def log(
    session: Session,
    principal: Principal,
    client_id: int,
    happened_on: date,
    minutes: int,
    summary: str = "",
    channel: str = "office",
    case_id: Optional[int] = None,
    hourly_rate: Optional[Decimal] = None,
    free: bool = False,
    free_reason: str = "",
    user_id: Optional[int] = None,
) -> Consult:
    """记一次咨询。"""
    if not principal.can("consult.log"):
        raise DomainError("你没有记咨询的权限", code="consult.forbidden")
    if channel not in CHANNELS:
        raise DomainError(
            "咨询方式必须是 {0} 之一".format("/".join(CHANNELS)),
            code="consult.bad_channel")
    if minutes <= 0:
        raise DomainError("咨询时长要大于 0", code="consult.bad_minutes")
    if minutes > 8 * 60:
        raise DomainError(
            "一次咨询记了 {0} 小时 —— 多半是填错了。\n"
            "分成几条记，或者确认一下时长。".format(round(minutes / 60, 1)),
            code="consult.too_long")

    # ★ 客户必须存在且是本所的 —— 收了钱得知道是谁的钱
    client = _crm().get_client(session, client_id)
    if client.tenant_id != principal.tenant_id:
        raise DomainError("客户不属于本律所", code="consult.cross_tenant")

    # ★ 挂了案件就要检查案件可见性 —— 否则可以拿咨询记录当探针，
    #   试出某个隔离墙后面存在哪些案子。
    if case_id:
        api = _case_api()
        if api is not None:
            api.assert_visible(session, principal, case_id)
            api.assert_writable(session, principal, case_id)

    # ★ 免费咨询要写明为什么。
    #   不写理由的免费，事后没人说得清是优惠还是漏收。
    if free and not free_reason.strip():
        raise DomainError(
            "免费咨询要写明原因（首次面谈 / 老客户 / 未成案 等）。\n"
            "不写理由的免费，事后分不清是优惠还是漏收。",
            code="consult.free_needs_reason")

    rate = ZERO if free else (money(hourly_rate) if hourly_rate is not None
                              else rate_for(session, principal, user_id))
    bmin = 0 if free else billable_minutes(minutes)
    amount = ZERO if free else money(rate * Decimal(bmin) / Decimal(60))

    row = Consult(
        tenant_id=principal.tenant_id,
        user_id=user_id or principal.user_id,
        client_id=client_id,
        case_id=case_id,
        happened_on=happened_on,
        minutes=minutes,
        channel=channel,
        summary=summary.strip(),
        hourly_rate=rate,
        amount=amount,
        free=free,
        free_reason=free_reason.strip(),
        status="draft",
        created_by=principal.user_id,
    )
    session.add(row)
    session.flush()
    return row


def update(session: Session, principal: Principal, consult_id: int,
           **patch) -> Consult:
    """改一条还没出账的咨询。"""
    row = _get(session, principal, consult_id)
    _assert_editable(row)
    if row.user_id != principal.user_id and not principal.can("consult.view_all"):
        raise DomainError("只能改自己的咨询记录", code="consult.forbidden")

    for field in ("happened_on", "minutes", "summary", "channel",
                  "case_id", "free", "free_reason"):
        if field in patch and patch[field] is not None:
            setattr(row, field, patch[field])

    if "hourly_rate" in patch and patch["hourly_rate"] is not None:
        row.hourly_rate = money(patch["hourly_rate"])

    # 时长或费率动了就重算金额 —— 但只在**还没出账**的时候
    if row.free:
        row.hourly_rate = ZERO
        row.amount = ZERO
    else:
        bmin = billable_minutes(row.minutes)
        row.amount = money(row.hourly_rate * Decimal(bmin) / Decimal(60))
    session.flush()
    return row


def void(session: Session, principal: Principal, consult_id: int,
         reason: str = "") -> Consult:
    """作废。★ 已出账的也能作废 —— 但要走 billing 那边红冲，这里只记状态。"""
    row = _get(session, principal, consult_id)
    if row.status == "void":
        return row
    if not reason.strip():
        raise DomainError("作废要写原因", code="consult.void_needs_reason")
    row.status = "void"
    row.summary = (row.summary + "\n[作废] " + reason.strip()).strip()
    session.flush()
    return row


# ---------------------------------------------------------------- 查询


def _scoped(session: Session, principal: Principal):
    stmt = select(Consult).where(Consult.tenant_id == principal.tenant_id)
    if not principal.can("consult.view_all"):
        stmt = stmt.where(Consult.user_id == principal.user_id)
    return stmt


def list_consults(session: Session, principal: Principal,
                  client_id: Optional[int] = None,
                  case_id: Optional[int] = None,
                  since: Optional[date] = None,
                  until: Optional[date] = None,
                  status: str = "",
                  limit: int = 200) -> List[Consult]:
    stmt = _scoped(session, principal)
    if client_id:
        stmt = stmt.where(Consult.client_id == client_id)
    if case_id:
        stmt = stmt.where(Consult.case_id == case_id)
    if since:
        stmt = stmt.where(Consult.happened_on >= since)
    if until:
        stmt = stmt.where(Consult.happened_on <= until)
    if status:
        stmt = stmt.where(Consult.status == status)
    return list(session.scalars(
        stmt.order_by(Consult.happened_on.desc(), Consult.id.desc()).limit(limit)))


def by_client(session: Session, principal: Principal,
              client_id: int) -> Dict[str, Any]:
    """一个客户咨询过几次、总共多少钱。"""
    rows = [r for r in list_consults(session, principal, client_id=client_id)
            if r.status != "void"]
    return {
        "client_id": client_id,
        "count": len(rows),
        "minutes": sum(r.minutes for r in rows),
        "amount": str(money(sum((r.amount for r in rows), ZERO))),
        "free_count": sum(1 for r in rows if r.free),
        "items": [describe(r) for r in rows],
    }


# ---------------------------------------------------------------- 给 billing 用


def billable(session: Session, principal: Principal,
             client_id: int, case_id: Optional[int] = None,
             since: Optional[date] = None,
             until: Optional[date] = None) -> List[Consult]:
    """哪些咨询能进账单 —— **这条规则归 consult 自己判**。

    ★ 免费的、已出账的、作废的都不能再进。
      billing 不该知道这些状态是怎么回事。
    """
    rows = list_consults(session, principal, client_id=client_id,
                         case_id=case_id, since=since, until=until)
    return [r for r in rows
            if r.status == "draft" and not r.free and r.amount > ZERO]


def mark_billed(session: Session, principal: Principal,
                ids: List[int], invoice_no: str) -> int:
    """出账后把行锁上。★ 只有 consult 自己能做这个动作。"""
    n = 0
    for cid in ids:
        row = session.get(Consult, cid)
        if row is None or row.tenant_id != principal.tenant_id:
            continue
        if row.status != "draft":
            continue
        row.status = "billed"
        row.invoice_no = invoice_no
        n += 1
    session.flush()
    return n


# ---------------------------------------------------------------- 看板


def metrics(session, principal, spec=None):
    """本期咨询费。★ 和办案的钱分开 —— 混在一起就看不出咨询这块值不值得做。"""
    spec = spec or {}
    since = spec.get("since")
    until = spec.get("until")

    stmt = select(Consult).where(
        Consult.tenant_id == principal.tenant_id,
        Consult.status != "void",
    )
    rows = list(session.scalars(stmt))
    if not principal.can("consult.view_all") or spec.get("user_id"):
        who = spec.get("user_id") or principal.user_id
        rows = [r for r in rows if r.user_id == who]
    if since:
        rows = [r for r in rows if r.happened_on >= since]
    if until:
        rows = [r for r in rows if r.happened_on <= until]

    total = money(sum((r.amount for r in rows), ZERO))
    return {
        "consult.received": {"value": str(total), "text": "¥%s" % total},
    }


# ---------------------------------------------------------------- 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的咨询记录。"""
    rows = list_consults(session, principal, case_id=case_id)
    rows = [r for r in rows if r.status != "void"]
    if not rows:
        return []
    return [{
        "kind": "consult",
        "label": "法律咨询",
        "count": len(rows),
        "detail": "共 {0} 分钟".format(sum(r.minutes for r in rows)),
    }]


def archive_blockers(session, principal, case_id):
    """归档前：还有没出账的咨询吗。"""
    rows = [r for r in list_consults(session, principal, case_id=case_id)
            if r.status == "draft" and not r.free and r.amount > ZERO]
    if not rows:
        return []
    return [{
        "kind": "consult.unbilled",
        "message": "还有 {0} 条咨询没出账（共 ¥{1}）".format(
            len(rows), money(sum((r.amount for r in rows), ZERO))),
        "how": "在案件的「咨询」里出账，或标为免费",
        "blocking": False,     # 提醒，不拦
    }]


# ---------------------------------------------------------------- 展示


def describe(row: Consult) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "client_id": row.client_id,
        "case_id": row.case_id,
        "happened_on": row.happened_on.isoformat(),
        "minutes": row.minutes,
        "billable_minutes": 0 if row.free else billable_minutes(row.minutes),
        "channel": row.channel,
        "channel_label": CHANNEL_LABELS.get(row.channel, row.channel),
        "summary": row.summary,
        "hourly_rate": str(money(row.hourly_rate)),
        "amount": str(money(row.amount)),
        "free": row.free,
        "free_reason": row.free_reason,
        "status": row.status,
        "status_label": STATUS_LABELS.get(row.status, row.status),
        "invoice_no": row.invoice_no,
    }
