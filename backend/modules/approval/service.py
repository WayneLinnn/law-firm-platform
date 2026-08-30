# -*- coding: utf-8 -*-
"""单据流转的业务层。

**头号原则：财务只看得到单据和金额，看不到案情。**

这是手册里给 Step 18 标的那个坑，也是这个模块存在的主要理由 ——
不然一张请款单用邮件发发也就完了。

★ **但要说清这道墙管到哪儿**：它管的是「处理一笔付款时不必知道是哪个案子」。
  财务角色目前仍有 case.view（全所），主动去案件页照样看得到 ——
  这是所里的管理决定，本模块不越权改它。
  **别把这道墙当成「财务无论如何接触不到案情」**，那是另一回事，
  真要做得先把 billing/time 里的可见性口径整个改一遍。

隔离靠**结构**不靠权限：ticket 上根本没有案情字段（见 models.py）。
剩下唯一的泄漏口是律师顺手把案件名称写进「事由」，所以有
`check_subject_leak()` 拦这一下。

**第二原则：链条在提交时固化。** 规则后来改了，已经在走的单子不跟着变 ——
否则一张走到一半的单子会突然多出或少掉一道，而没人说得清它该几道。

**第三原则：钱付出去就回写账务。** 请款单走完 → 自动在 billing 挂一笔
代垫费用等着开票。不这么做，垫钱和开账单差着几个月，全靠人记得。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.approval.models import (
    LOCKED_TICKET_STATUSES,
    SEAL_KIND_LABELS,
    SEAL_KINDS,
    TICKET_KIND_LABELS,
    TICKET_KINDS,
    TICKET_STATUS_LABELS,
    FlowRule,
    Ticket,
    TicketStep,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


def _case_api():
    return registry.api("case")


def _billing_api():
    """billing 是**可选**依赖：没装也能走单，只是付完款不会自动回写代垫。"""
    return registry.optional_api("billing")


def _identity_api():
    return registry.api("identity")


def money(x) -> Decimal:
    api = _billing_api()
    if api is not None:
        return api.money(x)
    return Decimal(str(x or 0)).quantize(Decimal("0.01"))


def _writable(session: Session, principal: Principal, case_id) -> None:
    """写操作走这句：看得见 **且** 写得动。归档/终止的案子只读。"""
    if case_id:
        _case_api().assert_writable(session, principal, case_id)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _can_see_case(session: Session, principal: Principal,
                  case_id: Optional[int]) -> bool:
    if not case_id:
        return True
    try:
        _case_api().assert_visible(session, principal, case_id)
        return True
    except Exception:                                    # noqa: BLE001
        return False


# ================================================================ 流转规则


#: 内置流转链。★ 是**起点不是终点** —— 律所自己改门槛、加环节。
DEFAULT_FLOWS = (
    ("payment", "请款 · 五千以下", "0", "5000",
     "团队负责人:approval.approve_team"),
    ("payment", "请款 · 五千以上", "5000", None,
     "团队负责人:approval.approve_team|管委会:approval.approve_firm"),
    ("reimburse", "报销 · 一千以下", "0", "1000",
     "团队负责人:approval.approve_team"),
    ("reimburse", "报销 · 一千以上", "1000", None,
     "团队负责人:approval.approve_team|管委会:approval.approve_firm"),
    # ★ 用印链只有**一道审批**，用印管理不是审批环节。
    #
    #   第一版把「用印管理」也写成一道，结果用印管理员要先点一次"通过"、
    #   再点一次"执行用印"——同一个人对同一件事点两次，
    #   第二次必然变成走过场。**审批和执行是两件事，但不该由同一个人做两遍。**
    #   批的是团队负责人，盖章的是用印管理，各做一次。
    ("seal", "用印", "0", None,
     "团队负责人:approval.approve_team"),
    ("disburse", "付款给第三方", "0", None,
     "团队负责人:approval.approve_team|管委会:approval.approve_firm"),
    ("other", "其他事项", "0", None,
     "团队负责人:approval.approve_team"),
)


def ensure_default_flows(session: Session, tenant_id: int) -> List[FlowRule]:
    """把内置流转链灌进本所。已存在的不覆盖 —— 律所可能改过门槛。"""
    have = {(r.kind, str(r.min_amount)) for r in session.scalars(
        select(FlowRule).where(FlowRule.tenant_id == tenant_id)).all()}
    out: List[FlowRule] = []
    for kind, name, lo, hi, steps in DEFAULT_FLOWS:
        key = (kind, str(Decimal(lo).quantize(Decimal("0.01"))))
        if key in have:
            continue
        row = FlowRule(
            tenant_id=tenant_id, kind=kind, name=name,
            min_amount=Decimal(lo), max_amount=Decimal(hi) if hi else None,
            steps=steps,
        )
        session.add(row)
        out.append(row)
    session.flush()
    # 返回本所现有全套 —— 幂等接口的返回值要描述**结果**不是增量
    return list(session.scalars(select(FlowRule).where(
        FlowRule.tenant_id == tenant_id).order_by(
            FlowRule.kind, FlowRule.min_amount)).all())


def parse_steps(spec: str) -> List[Tuple[str, str]]:
    """把 "名字:权限|名字:权限" 拆开。"""
    out: List[Tuple[str, str]] = []
    for part in (spec or "").split("|"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise DomainError(
                "流转链写错了：「{0}」应当是「名字:权限」的形式".format(part),
                code="approval.bad_flow_spec")
        name, perm = part.split(":", 1)
        out.append((name.strip(), perm.strip()))
    if not out:
        raise DomainError("流转链不能是空的", code="approval.bad_flow_spec")
    return out


def flow_for(session: Session, tenant_id: int, kind: str,
             amount: Decimal) -> FlowRule:
    """按单据类型和金额找流转链。

    ★ 找不到就**报错**，不给一条默认的"谁都能批"兜底。
      兜底链看着体贴，实际是把一张没人配过规则的单子悄悄放行了 ——
      而钱一旦付出去，没人会回头问"这单当初是按哪条规则批的"。
    """
    rows = list(session.scalars(select(FlowRule).where(
        FlowRule.tenant_id == tenant_id, FlowRule.kind == kind,
        FlowRule.active.is_(True),
    ).order_by(FlowRule.min_amount.desc())).all())
    for r in rows:
        if amount >= r.min_amount and (r.max_amount is None or amount < r.max_amount):
            return r
    raise DomainError(
        "本所没有配「{0}」在 {1} 元这个档位的审批流程，这张单子没法提交。\n"
        "请让管委会先配一条流转规则。".format(
            TICKET_KIND_LABELS.get(kind, kind), amount),
        code="approval.no_flow_rule")


def create_flow(session: Session, principal: Principal, kind: str, name: str,
                steps: str, min_amount=0, max_amount=None,
                note: str = "") -> FlowRule:
    if not principal.can("approval.manage_flow"):
        raise DomainError("配流转规则需要 approval.manage_flow 权限",
                          code="approval.forbidden")
    if kind not in TICKET_KINDS:
        raise DomainError("单据类型只能是：{0}".format(
            "、".join("{0}（{1}）".format(k, TICKET_KIND_LABELS[k])
                     for k in TICKET_KINDS)), code="approval.bad_kind")
    parse_steps(steps)                       # 先校验写法
    row = FlowRule(
        tenant_id=principal.tenant_id, kind=kind, name=name, steps=steps,
        min_amount=money(min_amount),
        max_amount=money(max_amount) if max_amount is not None else None,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def list_flows(session: Session, principal: Principal,
               kind: str = "") -> List[FlowRule]:
    stmt = select(FlowRule).where(FlowRule.tenant_id == principal.tenant_id)
    if kind:
        stmt = stmt.where(FlowRule.kind == kind)
    return list(session.scalars(
        stmt.order_by(FlowRule.kind, FlowRule.min_amount)).all())


# ================================================================ ★ 隔离墙


def check_subject_leak(session: Session, principal: Principal,
                       case_id: Optional[int], *texts: str) -> None:
    """★ 拦住把案情写进单据的那一下。

    ticket 上没有案情字段（结构隔离），但「事由」这一栏是自由文本，
    而它会跟着单子在所里流转 —— 财务、行政、用印管理都看得到。
    律师顺手写成「宏远诉凯达工程款纠纷的鉴定费」，隔离墙就白建了。

    **不是不信任律师，是这一栏太顺手了。** 系统能认出来的就拦一下：
    案件标题、当事人名字出现在事由里，直接拒绝并给出改法。

    ★ 只拦**认得出来**的。案由、争议焦点这些系统看不出，
      所以界面上还写着一句「这栏财务看得到」—— 提示和拦截都要有，
      因为拦截必然漏，而漏掉的那些只能靠人自觉。
    """
    if not case_id:
        return
    try:
        case = _case_api().get_case(session, case_id)
    except Exception:                                    # noqa: BLE001
        return

    blob = "".join(t or "" for t in texts)
    if not blob:
        return

    hits: List[str] = []
    title = (getattr(case, "title", "") or "").strip()
    # 两个字的标题太容易误伤（"离婚"），四个字往上才当特征
    if len(title) >= 4 and title in blob:
        hits.append("案件名称「{0}」".format(title))

    crm = registry.optional_api("crm")
    if crm is not None:
        try:
            for cp in _case_api().parties_of(session, case_id):
                party = crm.get_party(session, cp.party_id)
                nm = (getattr(party, "name", "") or "").strip()
                # 两个字的名字（"张三"是三个字）容易误伤，三个字往上才当特征
                if len(nm) >= 3 and nm in blob:
                    hits.append("当事人「{0}」".format(nm))
        except Exception as exc:                         # noqa: BLE001
            logger.warning("取当事人失败 case=%s: %s", case_id, exc)

    if hits:
        raise DomainError(
            "事由里出现了 {0}。\n"
            "★ 这一栏会跟着单子在所里流转，**财务、行政、用印管理都看得到** —— "
            "他们处理这笔钱不需要知道是哪个案子。\n"
            "改成不透露案情的写法，例如「诉讼费垫付」「鉴定费垫付」"
            "「保全担保费」。案件关联系统已经存了，不用写在事由里。".format(
                "、".join(dict.fromkeys(hits))),
            code="approval.subject_leaks_case")


# ================================================================ 单据


def _next_number(session: Session, tenant_id: int, kind: str,
                 today: date) -> str:
    prefix = "{0}{1}{2:02d}".format(
        {"payment": "QK", "reimburse": "BX", "seal": "YY",
         "disburse": "FK"}.get(kind, "QT"), today.year, today.month)
    n = session.scalar(select(func.count()).select_from(Ticket).where(
        Ticket.tenant_id == tenant_id, Ticket.number.like(prefix + "%"))) or 0
    return "{0}-{1:04d}".format(prefix, n + 1)


def create_ticket(session: Session, principal: Principal, kind: str,
                  subject: str, amount=0, case_id: Optional[int] = None,
                  payee: str = "", payee_account: str = "",
                  need_by: Optional[date] = None, detail: str = "",
                  doc_ids: str = "", seal_kind: str = "",
                  seal_document: str = "", seal_copies: int = 0,
                  seal_takeaway: bool = False,
                  today: Optional[date] = None) -> Ticket:
    """提一张单子（草稿）。

    ★ 事由**不自动从案件带出来** —— 那是最方便、也最会泄漏的做法。
      省下的两秒钟，代价是每张单子都带着案由标题在所里流转。
    """
    if not principal.can("approval.request"):
        raise DomainError("提单需要 approval.request 权限",
                          code="approval.forbidden")
    if kind not in TICKET_KINDS:
        raise DomainError("单据类型只能是：{0}".format(
            "、".join("{0}（{1}）".format(k, TICKET_KIND_LABELS[k])
                     for k in TICKET_KINDS)), code="approval.bad_kind")
    if not (subject or "").strip():
        raise DomainError("事由不能为空 —— 一句话说清这笔钱是干什么的",
                          code="approval.subject_required")
    if case_id:
        _case_api().assert_writable(session, principal, case_id)

    # ★ 隔离墙的那道检查
    check_subject_leak(session, principal, case_id, subject, detail)

    amt = money(amount)
    if kind in ("payment", "reimburse", "disburse") and amt <= ZERO:
        raise DomainError("这类单据必须写明金额", code="approval.amount_required")

    if kind == "seal":
        if seal_kind not in SEAL_KINDS:
            raise DomainError("印章种类只能是：{0}".format(
                "、".join("{0}（{1}）".format(k, SEAL_KIND_LABELS[k])
                         for k in SEAL_KINDS)), code="approval.bad_seal_kind")
        if not (seal_document or "").strip():
            raise DomainError("用印要写明盖在什么文件上",
                              code="approval.seal_document_required")
        if seal_copies <= 0:
            raise DomainError("用印要写明申请份数 —— 实际用印时要按这个数核对",
                              code="approval.seal_copies_required")

    today = today or date.today()
    row = Ticket(
        tenant_id=principal.tenant_id,
        number=_next_number(session, principal.tenant_id, kind, today),
        kind=kind, case_id=case_id, subject=subject.strip()[:255],
        amount=amt, payee=payee.strip()[:120],
        payee_account=payee_account.strip()[:64], need_by=need_by,
        detail=detail, doc_ids=doc_ids,
        seal_kind=seal_kind, seal_document=seal_document.strip()[:255],
        seal_copies=seal_copies, seal_takeaway=seal_takeaway,
        status="draft", requested_by=principal.user_id,
    )
    session.add(row)
    session.flush()
    return row


def _get(session: Session, principal: Principal, ticket_id: int) -> Ticket:
    row = session.scalar(select(Ticket).where(
        Ticket.id == ticket_id, Ticket.tenant_id == principal.tenant_id))
    if row is None:
        raise NotFound("单据不存在", code="approval.not_found")
    return row


def submit(session: Session, principal: Principal, ticket_id: int) -> Ticket:
    """提交，固化流转链。

    ★ 链条在**这一刻**定下来。规则后来改了（门槛从五千提到一万），
      已经在走的单子不跟着变 —— 否则一张走到一半的单子会突然多出
      或少掉一道，而没人说得清它到底该几道。
    """
    row = _get(session, principal, ticket_id)
    if row.requested_by != principal.user_id and not principal.can("approval.manage_flow"):
        raise DomainError("只能提交自己的单据", code="approval.not_yours")
    if row.status != "draft":
        raise DomainError("这张单子已经是「{0}」，不能再提交".format(
            TICKET_STATUS_LABELS.get(row.status, row.status)),
            code="approval.bad_status")

    rule = flow_for(session, principal.tenant_id, row.kind, row.amount)
    steps = parse_steps(rule.steps)

    for i, (name, perm) in enumerate(steps, start=1):
        session.add(TicketStep(
            tenant_id=principal.tenant_id, ticket_id=row.id, seq=i,
            name=name, required_perm=perm, status="waiting"))

    row.status = "pending"
    row.current_seq = 1
    row.requested_at = _now()
    session.flush()
    return row


def steps_of(session: Session, principal: Principal,
             ticket_id: int) -> List[TicketStep]:
    _get(session, principal, ticket_id)
    return list(session.scalars(select(TicketStep).where(
        TicketStep.ticket_id == ticket_id).order_by(TicketStep.seq)).all())


def decide(session: Session, principal: Principal, ticket_id: int,
           approve: bool, note: str = "") -> Ticket:
    """批 / 驳当前这一道。

    ★ 驳回**必须写理由**。一张被驳回却不说为什么的单子，
      申请人只会原样再提一次，来回三趟谁都烦，最后变成闭眼批。

    ★ 不能批自己提的单 —— 自己批自己等于没有审批。
    """
    row = _get(session, principal, ticket_id)
    if row.status != "pending":
        raise DomainError("这张单子现在是「{0}」，没有待批的环节".format(
            TICKET_STATUS_LABELS.get(row.status, row.status)),
            code="approval.bad_status")
    if row.requested_by == principal.user_id:
        raise DomainError("不能批自己提的单", code="approval.self_approve")

    step = session.scalar(select(TicketStep).where(
        TicketStep.ticket_id == ticket_id, TicketStep.seq == row.current_seq))
    if step is None:
        raise NotFound("找不到当前环节", code="approval.step_not_found")
    if not principal.can(step.required_perm):
        raise DomainError(
            "这一道是「{0}」，需要 {1} 权限。".format(step.name, step.required_perm),
            code="approval.forbidden")
    if not approve and not note.strip():
        raise DomainError("驳回必须写明理由 —— 不说为什么，对方只会原样再提一次",
                          code="approval.reject_note_required")

    step.status = "approved" if approve else "rejected"
    step.decided_by = principal.user_id
    step.decided_at = _now()
    step.note = note.strip()[:255]

    if not approve:
        row.status = "rejected"
        row.close_note = note.strip()[:255]
        session.flush()
        return row

    nxt = session.scalar(select(TicketStep).where(
        TicketStep.ticket_id == ticket_id, TicketStep.seq == row.current_seq + 1))
    if nxt is None:
        # 走完了。★ 走完不等于付完钱 —— 付款是另一个动作，另一个人
        row.status = "approved"
    else:
        row.current_seq += 1
    session.flush()
    return row


def pay(session: Session, principal: Principal, ticket_id: int,
        paid_on: Optional[date] = None, reference: str = "",
        today: Optional[date] = None) -> Ticket:
    """财务付款，并**回写账务**。

    ★ 付款和审批分开，是因为它们是两件事：
      审批说的是"这笔钱该不该花"，付款说的是"钱确实出去了"。
      合成一个动作，就没法回答"批了但还没付"的那批单子有多少 ——
      而那批单子正是财务每天要盯的。
    """
    if not principal.can("approval.pay"):
        raise DomainError("付款需要 approval.pay 权限", code="approval.forbidden")
    today = today or date.today()
    row = _get(session, principal, ticket_id)
    if row.status != "approved":
        raise DomainError(
            "只有「已批待付」的单子能付款，这张现在是「{0}」".format(
                TICKET_STATUS_LABELS.get(row.status, row.status)),
            code="approval.bad_status")
    if paid_on and paid_on > today:
        raise DomainError("不能登记未来的付款（{0}）".format(paid_on),
                          code="approval.future_payment")

    row.status = "paid"
    row.paid_by = principal.user_id
    row.paid_on = paid_on or today
    row.pay_reference = reference.strip()[:128]

    # ★ 回写账务：请款/付款类的钱一出去，就在 billing 挂一笔代垫等着开票。
    #   不这么做，垫钱和开账单差着好几个月，全靠人记得 —— 记漏了律所自己贴。
    if row.kind in ("payment", "disburse") and row.case_id:
        api = _billing_api()
        if api is None:
            logger.warning("没装 billing，单据 %s 的代垫没能回写", row.number)
        else:
            d = api.record_disbursement(
                session, principal, row.case_id, row.amount,
                row.subject, spent_on=row.paid_on, ticket_id=row.id,
                paid_by=row.requested_by,
                note="来自单据 {0}".format(row.number))
            row.disbursement_id = d.id
    session.flush()
    return row


def execute_seal(session: Session, principal: Principal, ticket_id: int,
                 used_copies: int, note: str = "") -> Ticket:
    """用印执行：回填**实际盖了几份**。

    ★ 这一步不是走个过场。用印是律所的重管控事项 ——
      申请 3 份盖了 5 份，那两份盖了什么、给了谁，事后查不清就是事故。
      所以实际份数必须回填，而且**超过申请份数直接拦**。
    """
    if not principal.can("approval.seal"):
        raise DomainError("用印执行需要 approval.seal 权限",
                          code="approval.forbidden")
    row = _get(session, principal, ticket_id)
    if row.kind != "seal":
        raise DomainError("这不是用印单", code="approval.not_seal")
    if row.status != "approved":
        raise DomainError(
            "只有「已批待付」的用印单能执行，这张现在是「{0}」".format(
                TICKET_STATUS_LABELS.get(row.status, row.status)),
            code="approval.bad_status")
    if used_copies <= 0:
        raise DomainError("实际用印份数必须大于 0", code="approval.bad_copies")
    if used_copies > row.seal_copies:
        raise DomainError(
            "实际盖了 {0} 份，超过申请的 {1} 份。\n"
            "★ 多盖的那 {2} 份盖在了什么上、给了谁，事后查不清就是事故。"
            "请先补一张用印单，或者说明情况后由管委会处理。".format(
                used_copies, row.seal_copies, used_copies - row.seal_copies),
            code="approval.seal_over_used")

    row.seal_used = used_copies
    row.status = "done"
    row.paid_by = principal.user_id
    row.paid_on = date.today()
    if note:
        row.close_note = note.strip()[:255]
    session.flush()
    return row


def cancel(session: Session, principal: Principal, ticket_id: int,
           reason: str) -> Ticket:
    """作废。★ 不删除 —— 一张凭空消失的单据比一张错的更可疑。"""
    if not reason.strip():
        raise DomainError("作废必须写明原因", code="approval.cancel_reason_required")
    row = _get(session, principal, ticket_id)
    if row.status in LOCKED_TICKET_STATUSES:
        raise DomainError(
            "这张单子已经是「{0}」，作废不了。已经付出去的钱要冲回，"
            "请走财务的冲账流程。".format(
                TICKET_STATUS_LABELS.get(row.status, row.status)),
            code="approval.locked")
    if row.requested_by != principal.user_id and not principal.can("approval.approve_firm"):
        raise DomainError("只能作废自己的单据", code="approval.not_yours")
    row.status = "cancelled"
    row.close_note = reason.strip()[:255]
    session.flush()
    return row


# ================================================================ 查询


def my_tickets(session: Session, principal: Principal,
               status: str = "") -> List[Ticket]:
    stmt = select(Ticket).where(
        Ticket.tenant_id == principal.tenant_id,
        Ticket.requested_by == principal.user_id)
    if status:
        stmt = stmt.where(Ticket.status == status)
    return list(session.scalars(stmt.order_by(Ticket.id.desc())).all())


def inbox(session: Session, principal: Principal) -> List[Ticket]:
    """待我处理的单据。

    ★ 一个人能不能批某一道，看的是**权限**不是指派。
      指派到人的做法在律所里会卡住：负责人出差三天，所有单子停摆。
      按权限走，团队里任何一个有权限的人都能批 —— 谁批的记在链上。
    """
    rows = list(session.scalars(select(Ticket).where(
        Ticket.tenant_id == principal.tenant_id,
        Ticket.status.in_(["pending", "approved"]),
    ).order_by(Ticket.id.desc())).all())

    out: List[Ticket] = []
    for t in rows:
        if t.requested_by == principal.user_id:
            continue                                     # 自己的单自己批不了
        if t.status == "pending":
            step = session.scalar(select(TicketStep).where(
                TicketStep.ticket_id == t.id, TicketStep.seq == t.current_seq))
            if step is not None and principal.can(step.required_perm):
                out.append(t)
        elif t.status == "approved":
            # 已批待付：给财务和用印管理看
            if t.kind == "seal":
                if principal.can("approval.seal"):
                    out.append(t)
            elif principal.can("approval.pay"):
                out.append(t)
    return out


def tickets_of_case(session: Session, principal: Principal,
                    case_id: int) -> List[Ticket]:
    _case_api().assert_visible(session, principal, case_id)
    return list(session.scalars(select(Ticket).where(
        Ticket.tenant_id == principal.tenant_id, Ticket.case_id == case_id
    ).order_by(Ticket.id.desc())).all())


# ================================================================ 展示


def _mask_account(acct: str) -> str:
    """账号掩码：只留后四位。"""
    a = (acct or "").strip()
    if len(a) <= 4:
        return "****" if a else ""
    return "*" * (len(a) - 4) + a[-4:]


def describe(session: Session, principal: Principal, t: Ticket) -> Dict[str, Any]:
    """给界面用的一张单据。

    ★ **隔离墙在这里落地。**

      看不见关联案件的人（典型就是财务），拿到的 payload 里没有 case_id。
      但要**明确告诉他"有一个案件，你看不到"** —— 而不是把这个字段
      悄悄抹成 null。

      悄悄抹掉的后果是：财务以为这单没挂案子，于是自己在备注里问
      "这是哪个案子的"，然后律师在备注里答了 —— 隔离墙就从备注栏漏光了。
      **明说看不到，人才不会去猜、去问、去补。**
    """
    see_case = _can_see_case(session, principal, t.case_id)
    show_account = principal.can("approval.pay")

    out = {
        "id": t.id, "number": t.number, "kind": t.kind,
        "kind_label": TICKET_KIND_LABELS.get(t.kind, t.kind),
        "subject": t.subject,
        "amount": str(t.amount), "currency": t.currency,
        "payee": t.payee,
        # ★ 账号只对有付款权限的人显示完整 —— 其他人看到掩码
        "payee_account": t.payee_account if show_account
                         else _mask_account(t.payee_account),
        "account_masked": not show_account and bool(t.payee_account),
        "need_by": t.need_by.isoformat() if t.need_by else "",
        "detail": t.detail, "doc_ids": t.doc_ids,
        "status": t.status,
        "status_label": TICKET_STATUS_LABELS.get(t.status, t.status),
        "locked": t.status in LOCKED_TICKET_STATUSES,
        "current_seq": t.current_seq,
        "requested_by": t.requested_by,
        "requested_at": t.requested_at.isoformat() if t.requested_at else "",
        "paid_by": t.paid_by,
        "paid_on": t.paid_on.isoformat() if t.paid_on else "",
        "pay_reference": t.pay_reference,
        "close_note": t.close_note,
        "disbursement_id": t.disbursement_id,
        # 用印
        "seal_kind": t.seal_kind,
        "seal_kind_label": SEAL_KIND_LABELS.get(t.seal_kind, t.seal_kind),
        "seal_document": t.seal_document,
        "seal_copies": t.seal_copies, "seal_used": t.seal_used,
        "seal_takeaway": t.seal_takeaway,
    }

    if see_case:
        out["case_id"] = t.case_id
        out["case_visible"] = True
        out["case_note"] = ""
    else:
        out["case_id"] = None
        out["case_visible"] = False
        out["case_note"] = (
            "本单关联了一个案件，但你没有该案件的查看权限。\n"
            "★ 这不是错误 —— 处理这笔钱不需要知道是哪个案子。"
            "确需了解请找承办律师或管委会。")
    return out


def describe_step(s: TicketStep) -> Dict[str, Any]:
    return {
        "id": s.id, "seq": s.seq, "name": s.name,
        "required_perm": s.required_perm,
        "status": s.status,
        "status_label": {"waiting": "待处理", "approved": "已通过",
                         "rejected": "已驳回", "skipped": "已跳过"}.get(
                             s.status, s.status),
        "decided_by": s.decided_by,
        "decided_at": s.decided_at.isoformat() if s.decided_at else "",
        "note": s.note,
    }


def describe_flow(r: FlowRule) -> Dict[str, Any]:
    return {
        "id": r.id, "kind": r.kind,
        "kind_label": TICKET_KIND_LABELS.get(r.kind, r.kind),
        "name": r.name,
        "min_amount": str(r.min_amount),
        "max_amount": str(r.max_amount) if r.max_amount is not None else "",
        "steps": r.steps,
        "step_list": [{"name": n, "perm": p} for n, p in parse_steps(r.steps)],
        "active": r.active, "note": r.note,
    }


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的单据。"""
    _case_api().assert_visible(session, principal, case_id)
    rows = tickets_of_case(session, principal, case_id)
    live = [t for t in rows if t.status != "cancelled"]
    if not live:
        return []
    by_kind = {}
    for t in live:
        by_kind.setdefault(t.kind, []).append(t)
    return [{"name": "单据 · {0}".format(TICKET_KIND_LABELS.get(k, k)),
             "count": len(v), "note": ""}
            for k, v in sorted(by_kind.items())]


def archive_blockers(session, principal, case_id):
    """★ 还有单子在走 —— 归档后批不了、付不了。"""
    _case_api().assert_visible(session, principal, case_id)
    live = [t for t in tickets_of_case(session, principal, case_id)
            if t.status in ("draft", "pending", "approved")]
    if live:
        return [{"what": "还有 {0} 张单据没走完".format(len(live)),
                 "how": "走完或作废 —— 归档后批不了也付不了"}]
    return []


# ================================================================ 看板指标


def metrics(session, principal, spec=None):
    """待我批的、待付的 —— 都是"卡住了"的东西。"""
    spec = spec or {}
    mine = inbox(session, principal)
    pending = [t for t in mine if t.status == "pending"]
    topay = [t for t in mine if t.status == "approved"]
    return {
        "approval.to_decide": {"value": len(pending),
                               "text": "%d 张" % len(pending)},
        "approval.to_pay": {"value": len(topay), "text": "%d 张" % len(topay)},
    }


# ---------------------------------------------------------------- 统一待办

def inbox_items(session: Session, principal: Principal,
                today=None) -> List[Dict[str, Any]]:
    """报进统一待办中心的条目：**等我批的 + 等我付的**。

    ★ 单据的紧急度不看到期日 —— 它没有法定期限。
      看的是**卡了多久**：一张挂了一周的请款单，
      多半是所有人都以为别人会批。

    ★ `why_me` 要说清「为什么轮到我」。
      单据是**按权限**算的不是按指派 —— 团队里任何一个有权限的人都会看到它，
      不说清楚，每个人都会以为是别人的事。
    """
    from datetime import date as _date

    # ★ 权限门在模块这一侧 —— 汇总层不替别人判授权，理由见 task.inbox_items
    if not principal.can("approval.view"):
        return []

    today = today or _date.today()
    out: List[Dict[str, Any]] = []

    for t in inbox(session, principal):
        waited = 0
        if t.created_at:
            waited = (today - t.created_at.date()).days
        # ★ 卡三天以上就该显眼 —— 请款单卡住的是别人垫的钱
        urgency = "urgent" if waited >= 3 else "normal"
        kind_label = TICKET_KIND_LABELS.get(t.kind, t.kind)
        if t.status == "pending":
            title = "待批：{0}".format(t.subject)
            why = "你有这一道的审批权限"
        else:
            title = "待付：{0}".format(t.subject)
            why = "你有付款权限"
        detail = "{0}　¥{1}".format(kind_label, t.amount or 0)
        if waited > 0:
            detail += "　已等 {0} 天".format(waited)
        out.append({
            "key": "approval.ticket:{0}".format(t.id),
            "kind": "ticket",
            "title": title,
            "detail": detail,
            "due_on": "",
            "urgency": urgency,
            "link": "/tickets",
            # ★ 单据**刻意不挂案件** —— 财务处理付款不需要知道是哪个案子
            "case_id": None,
            "why_me": why,
        })
    return out
