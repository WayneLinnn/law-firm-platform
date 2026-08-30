# -*- coding: utf-8 -*-
"""待办与时限的业务层。

**时限这块的头号原则：算不出来要明说，绝不猜。**

算错的时限律师会信，算不出的他会自己去查。所以缺起算日期时，
状态是 `unknown` 并写清"缺哪个日期"，而不是拿一个近似的日期顶上。

第二原则：**产品上是提示，不是承诺。** 每条时限都带 `caveat`
（这条规则盖不住什么），并且要律师**确认**过才算数 ——
没确认的只能当提醒看。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.task import rules as builtin
from modules.task.models import (
    DEADLINE_STATUSES,
    PRIORITIES,
    TASK_STATUSES,
    Deadline,
    DeadlineRule,
    Task,
)

logger = logging.getLogger(__name__)

#: 起算日期字段的人话名字。缺哪个要说得出名字，不能只报英文 key
FIELD_LABELS = {
    "payable_on": "应付款日期",
    "completed_on": "竣工日期",
    "handover_on": "交付日期",
    "settled_on": "结算日期",
    "served_on": "收到裁判文书日期",
    "known_on": "知道权利受损害之日",
    "effective_on": "裁判文书生效日期",
}


def _case_api():
    return registry.api("case")


def _visible(session: Session, principal: Principal, case_id: Optional[int]) -> None:
    if case_id:
        _case_api().assert_visible(session, principal, case_id)


# ================================================================ 日期


def plus(anchor: date, months: int = 0, days: int = 0) -> date:
    """加月/加日。月末不足时退到当月最后一天（8/31 + 6 个月 = 2/28）。"""
    d = anchor
    if months:
        y, m = d.year, d.month + months
        y += (m - 1) // 12
        m = (m - 1) % 12 + 1
        day = d.day
        while day > 0:
            try:
                d = date(y, m, day)
                break
            except ValueError:
                day -= 1
    if days:
        from datetime import timedelta

        d = d + timedelta(days=days)
    return d


def _writable(session: Session, principal: Principal, case_id) -> None:
    """写操作走这句：看得见 **且** 写得动。归档/终止的案子只读。"""
    if case_id:
        _case_api().assert_writable(session, principal, case_id)


def parse_date(v: Any) -> Optional[date]:
    """认不出来就返回 None。**绝不猜。**"""
    if isinstance(v, date):
        return v
    if not v:
        return None
    import re

    m = re.search(r"(\d{4})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})", str(v))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ================================================================ 规则


def ensure_default_rules(session: Session, tenant_id: int) -> List[DeadlineRule]:
    """把内置规则灌进这个所。已存在的**不覆盖** —— 律所可能改过。"""
    existing = {r.code: r for r in session.scalars(
        select(DeadlineRule).where(DeadlineRule.tenant_id == tenant_id)).all()}
    # ★ out 收的是「本所现在拥有的全套内置规则」，不是「这次新建的那几条」。
    #   第二次调用如果只返回新建的（空列表），界面会以为一条规则都没有 ——
    #   一个幂等接口的返回值应该描述**结果状态**，不是这次的增量。
    out = []
    for r in builtin.BUILTIN:
        if r["code"] in existing:
            row = existing[r["code"]]
            # ★ 内置规则**补空**，但绝不覆盖律所改过的值。
            #
            #   这一段是被一次真事故逼出来的：给内置规则新加了「最长保护期
            #   20 年」这个字段，单元测试（干净库）全绿，但已经灌过规则的
            #   律所一条都没补上 —— 库里存的还是 0，界面照样显示「正常」。
            #   **新库对、老库错，而且没有任何报错**，这是最难发现的一类。
            #
            #   判断依据只看「这个字段是不是还停在默认值」：停在默认值说明
            #   律所从没碰过它，补上是补一个漏；不是默认值说明是律所的选择，
            #   碰它就是拿我们的意见覆盖人家的意见。
            for field, default in (("outer_years", 0),):
                if getattr(row, field) == default and r.get(field, default) != default:
                    setattr(row, field, r[field])
                    logger.info("补齐内置规则 %s 的 %s = %s",
                                row.code, field, r[field])
            out.append(row)
            continue
        row = DeadlineRule(
            tenant_id=tenant_id, code=r["code"], name=r["name"], basis=r["basis"],
            cause_code=r.get("cause_code", ""), role=r.get("role", ""),
            anchor_field=r["anchor_field"], months=r.get("months", 0),
            days=r.get("days", 0), fatal=r.get("fatal", False),
            suspendable=r.get("suspendable", False),
            outer_years=r.get("outer_years", 0),
            warn_days=r.get("warn_days", 30),
            caveat=r.get("caveat", ""), note=r.get("note", ""),
            builtin=True, active=True,
        )
        session.add(row)
        out.append(row)
    session.flush()
    return out


def list_rules(session: Session, principal: Principal,
               cause_code: str = "", include_inactive: bool = False) -> List[DeadlineRule]:
    stmt = select(DeadlineRule).where(DeadlineRule.tenant_id == principal.tenant_id)
    if not include_inactive:
        stmt = stmt.where(DeadlineRule.active.is_(True))
    if cause_code:
        stmt = stmt.where(DeadlineRule.cause_code.in_(["", cause_code]))
    return list(session.scalars(stmt.order_by(DeadlineRule.cause_code,
                                              DeadlineRule.code)).all())


def create_rule(session: Session, principal: Principal, **kw) -> DeadlineRule:
    """律所自己加规则。"""
    code = (kw.get("code") or "").strip()
    if not code:
        raise DomainError("规则代码不能为空", code="task.rule_code_required")
    if not (kw.get("basis") or "").strip():
        # ★ 没有依据的时限，律师不敢信也不该信
        raise DomainError("必须填法律依据 —— 算不出来路的期限没人敢用",
                          code="task.rule_basis_required")
    anchor_field = (kw.get("anchor_field") or "").strip()
    if not anchor_field:
        raise DomainError("必须指定从哪个日期起算", code="task.rule_anchor_required")
    # ★ 起算字段是**封闭词表**，不能随便写。两个理由，都不是洁癖：
    #   ① generate() 只会从流程抽出的要素里按这些字段名取值。写一个没人
    #      填的字段名，等于造了一条永远停在「算不出」的规则 —— 它会一直
    #      占着看板的红色计数，律师查半天发现根本填不了。
    #   ② 界面上要显示「缺『竣工验收日』」。词表外的字段没有中文名，
    #      律师看到的会是「缺『anchor_x'」，等于没提示。
    if anchor_field not in FIELD_LABELS:
        raise DomainError(
            "起算字段只能是：{0}".format(
                "、".join("{0}（{1}）".format(k, v) for k, v in FIELD_LABELS.items())),
            code="task.rule_anchor_unknown")
    if session.scalar(select(DeadlineRule).where(
            DeadlineRule.tenant_id == principal.tenant_id, DeadlineRule.code == code)):
        raise DomainError("规则代码已存在：{0}".format(code), code="task.rule_exists")

    row = DeadlineRule(
        tenant_id=principal.tenant_id, code=code,
        name=kw.get("name") or code, basis=kw["basis"],
        cause_code=kw.get("cause_code", ""), role=kw.get("role", ""),
        anchor_field=anchor_field,
        months=int(kw.get("months") or 0), days=int(kw.get("days") or 0),
        outer_years=int(kw.get("outer_years") or 0),
        fatal=bool(kw.get("fatal")), suspendable=bool(kw.get("suspendable")),
        warn_days=int(kw.get("warn_days") or 30),
        caveat=kw.get("caveat", ""), note=kw.get("note", ""),
        builtin=False, active=True, created_by=principal.user_id,
    )
    session.add(row)
    session.flush()
    return row


def set_rule_active(session: Session, principal: Principal,
                    code: str, active: bool) -> DeadlineRule:
    row = session.scalar(select(DeadlineRule).where(
        DeadlineRule.tenant_id == principal.tenant_id, DeadlineRule.code == code))
    if row is None:
        raise NotFound("规则不存在：{0}".format(code), code="task.rule_not_found")
    row.active = active
    session.flush()
    return row


# ================================================================ 时限


def outer_due(anchor: Optional[date], outer_years: int) -> Optional[date]:
    """最长保护期届满日 —— 从**原始起算日**算，中断中止都不影响它。"""
    if anchor is None or not outer_years:
        return None
    return plus(anchor, months=outer_years * 12)


def recompute(row: Deadline, rule: Optional[DeadlineRule],
              today: Optional[date] = None) -> Deadline:
    """把一条时限的届满日和状态重算一遍。

    ★ 中断、中止、最长保护期三件事都在这里收口，只此一处。
      分散在 generate/set_anchor/interrupt 各写一遍，迟早写出三种算法。

    算式（《民法典》188/194/195）：
      ① 基准日 = 中断之日，没有中断就是起算日      —— 195 条：中断后**重新起算**
      ② 届满日 = 基准日 + 规则期间
      ③ 有中止的，届满日不早于「事由消除之日 + 6 个月」 —— 194 条
      ④ ★ 最后拿最长保护期封顶 —— 188 条 2 款：自权利受损害之日起 20 年，
         **不适用中断中止**。这一步必须放在最后，否则中断能把它顶穿。
    """
    today = today or date.today()
    if row.status in ("done", "waived", "no_fixed"):
        return row

    has_period = bool(rule and (rule.months or rule.days))
    base = row.interrupted_on or row.anchor_on

    if base is None or not has_period:
        row.due_on = None
    else:
        due = plus(base, rule.months, rule.days)
        if row.suspend_end_on:
            # 194 条：自中止事由消除之日起满六个月，时效期间届满
            due = max(due, plus(row.suspend_end_on, months=6))
        row.due_on = due

    row.outer_due_on = outer_due(row.anchor_on, rule.outer_years if rule else 0)
    # ★ 封顶。中断了十次也顶不穿 20 年 —— 顶穿了就是在给假的安全感
    if row.due_on and row.outer_due_on and row.due_on > row.outer_due_on:
        row.due_on = row.outer_due_on

    row.status = _status_of(row, today)
    return row


def _status_of(d: Deadline, today: date) -> str:
    if d.status in ("done", "waived", "no_fixed"):
        return d.status
    if d.due_on is None:
        return "unknown"
    left = (d.due_on - today).days
    if left < 0:
        return "expired"
    if left <= (d.warn_days or 30):
        return "urgent"
    return "ok"


def generate(
    session: Session,
    principal: Principal,
    case_id: int,
    anchors: Optional[Dict[str, Any]] = None,
    role: str = "",
    today: Optional[date] = None,
) -> List[Deadline]:
    """按规则给案件生成/更新时限。

    `anchors` 是起算日期，通常来自 workflow 抽出并经律师确认的要素。

    ★ **这就是把「跑完就没」变成「长期挂在案件上」的那一步。**
      以前要素只活在某条流程的 checkpoint 里，跑第二条流程要重抽一遍，
      案件页上也看不见。现在确认过的日期落进时限表，长期做提醒。

    ★ 已经被律师确认过、或已登记中断的时限，**不覆盖起算日** ——
      人工判断优先于规则推算。
    """
    _writable(session, principal, case_id)
    today = today or date.today()
    anchors = anchors or {}

    case = _case_api().get_case(session, case_id)
    cause_code = ""
    if case.cause_id:
        try:
            cause_code = _case_api().get_cause(session, case.cause_id).code
        except Exception:                          # noqa: BLE001
            cause_code = ""

    out: List[Deadline] = []
    for rule in list_rules(session, principal, cause_code):
        if rule.role and role and rule.role != role:
            continue
        row = session.scalar(select(Deadline).where(
            Deadline.case_id == case_id, Deadline.rule_code == rule.code))

        anchor = parse_date(anchors.get(rule.anchor_field))
        if row is None:
            row = Deadline(
                tenant_id=principal.tenant_id, case_id=case_id, rule_code=rule.code,
                name=rule.name, basis=rule.basis, anchor_field=rule.anchor_field,
                warn_days=rule.warn_days, fatal=rule.fatal, caveat=rule.caveat,
                note=rule.note, source="rule",
            )
            session.add(row)
        elif row.confirmed_at or row.interrupted_on:
            # 人工确认过的不动 —— 律师的判断优先于规则推算
            out.append(row)
            continue

        # 规则可能被改过，同步过来
        row.name, row.basis = rule.name, rule.basis
        row.fatal, row.warn_days = rule.fatal, rule.warn_days
        row.caveat, row.note = rule.caveat, rule.note

        # ★ 有些规则本来就**没有固定期间**（质量责任是「设计使用年限内」）。
        #   第一版把它们和「缺起算日」一样标成 unknown，后果是：
        #   律师老老实实把竣工日期填进去，它**还是**红着 —— 因为根本没有
        #   期间可以加。一条永远变不成绿色的红字，两周之内就会被无视，
        #   然后旁边真正「算不出」的除斥期间跟着一起被无视。
        #   所以它要有自己的状态：不催，但也不消失。
        has_period = bool(rule.months or rule.days)
        row.anchor_on = anchor
        if not has_period:
            row.status = "no_fixed"
            row.due_on = None
        else:
            recompute(row, rule, today)
        session.flush()
        out.append(row)

    return sort_deadlines(out, today)


def sort_deadlines(rows: List[Deadline], today: Optional[date] = None) -> List[Deadline]:
    """要命的排前面。律师只看前两行也不能漏掉致命项。"""
    order = {"expired": 0, "urgent": 1, "unknown": 2, "ok": 3,
             "no_fixed": 4, "done": 5, "waived": 6}
    return sorted(rows, key=lambda d: (order.get(d.status, 9), not d.fatal,
                                       d.due_on or date(2999, 1, 1)))


def deadlines_of(session: Session, principal: Principal, case_id: int,
                 today: Optional[date] = None) -> List[Deadline]:
    _visible(session, principal, case_id)
    today = today or date.today()
    rows = list(session.scalars(select(Deadline).where(
        Deadline.case_id == case_id,
        Deadline.tenant_id == principal.tenant_id)).all())
    for r in rows:                                 # 每次读都刷新状态，日期天天在走
        r.status = _status_of(r, today)
    session.flush()
    return sort_deadlines(rows, today)


def set_anchor(session: Session, principal: Principal, deadline_id: int,
               anchor_on: Optional[date], note: str = "",
               today: Optional[date] = None) -> Deadline:
    """律师手工定起算日 —— 并视为**确认**。

    起算点常有争议（合同没约定付款时间时尤其），最终得由人拍板。
    """
    row = _get(session, principal, deadline_id)
    rule = _rule_of(session, principal, row)
    row.anchor_on = anchor_on
    row.note = note or row.note
    row.confirmed_by = principal.user_id
    row.confirmed_at = datetime.now(timezone.utc)
    recompute(row, rule, today)
    session.flush()
    return row


def _rule_of(session: Session, principal: Principal,
             row: Deadline) -> Optional[DeadlineRule]:
    return session.scalar(select(DeadlineRule).where(
        DeadlineRule.tenant_id == principal.tenant_id,
        DeadlineRule.code == row.rule_code))


def interrupt(session: Session, principal: Principal, deadline_id: int,
              on: date, note: str, today: Optional[date] = None) -> Deadline:
    """登记时效中断 —— 从这天重新起算。

    ★ 除斥期间**不能中断**，会直接拒绝。这不是配置项，是法律性质。
    """
    row = _get(session, principal, deadline_id)
    rule = session.scalar(select(DeadlineRule).where(
        DeadlineRule.tenant_id == principal.tenant_id,
        DeadlineRule.code == row.rule_code))
    # ★ 三种拒绝理由必须分开说，不能共用一句「是除斥期间」。
    #   律师会照着这句话去理解一条期限的**法律性质** —— 把一条普通的、
    #   只是没标可中断的规则说成除斥期间，是在界面上讲错法律。
    if row.fatal:
        raise DomainError(
            "「{0}」是除斥期间，不发生中断/中止 —— 登记中断不会延长它。".format(row.name),
            code="task.not_suspendable",
        )
    if row.status == "no_fixed" or not (rule and (rule.months or rule.days)):
        raise DomainError(
            "「{0}」没有固定期间，没有可以重新起算的东西 —— "
            "中断在这里没有意义。".format(row.name),
            code="task.no_fixed_period",
        )
    if not rule.suspendable:
        raise DomainError(
            "本所的规则没有把「{0}」标记为可中断。如果确实发生了中断事由，"
            "请让管委会改规则，或手工加一条时限。".format(row.name),
            code="task.rule_not_suspendable",
        )
    if not note.strip():
        raise DomainError("中断必须写明事由和证据 —— 事后要拿得出来",
                          code="task.interrupt_note_required")
    row.interrupted_on = on
    row.interrupt_note = note.strip()[:255]
    # ★ **不动 anchor_on**。原来这里把起算日改成了中断日，那样最长保护期
    #   就会跟着中断一起往后跑 —— 20 年上限被自己顶穿，等于没有上限。
    #   起算日是「权利受损害之日」这个**事实**，中断改变不了它。
    row.confirmed_by = principal.user_id
    row.confirmed_at = datetime.now(timezone.utc)
    recompute(row, rule, today)
    session.flush()
    return row


def suspend(session: Session, principal: Principal, deadline_id: int,
            end_on: date, note: str, today: Optional[date] = None) -> Deadline:
    """登记时效**中止**（《民法典》194 条）。

    ★ 和中断是两回事，界面上一定要分开摆：
        中断（195）—— 发生催告、起诉、对方同意履行 → 从那天**重新起算**整个期间
        中止（194）—— 最后六个月内发生不可抗力等障碍 → 障碍**消除**之日起满六个月届满

      律师最常搞混的是方向：中断把期限推得很远（重来一遍），
      中止只保证「至少还有六个月」。填错一个，结论差好几年。

    参数 end_on 是**事由消除之日**，不是发生之日 —— 194 条的算式只用得上消除日。
    """
    row = _get(session, principal, deadline_id)
    rule = _rule_of(session, principal, row)
    if row.fatal:
        raise DomainError(
            "「{0}」是除斥期间，不发生中断/中止 —— 登记中止不会延长它。".format(row.name),
            code="task.not_suspendable",
        )
    if row.status == "no_fixed" or not (rule and (rule.months or rule.days)):
        raise DomainError(
            "「{0}」没有固定期间，谈不上中止。".format(row.name),
            code="task.no_fixed_period",
        )
    if not note.strip():
        raise DomainError("中止必须写明障碍事由和证据 —— 事后要拿得出来",
                          code="task.suspend_note_required")
    row.suspend_end_on = end_on
    row.suspend_note = note.strip()[:255]
    row.confirmed_by = principal.user_id
    row.confirmed_at = datetime.now(timezone.utc)
    recompute(row, rule, today)
    session.flush()
    return row


def mark(session: Session, principal: Principal, deadline_id: int,
         status: str, note: str = "") -> Deadline:
    """标记已行使 / 不适用。"""
    if status not in ("done", "waived"):
        raise DomainError("只能标记为已行使(done)或不适用(waived)",
                          code="task.bad_mark")
    row = _get(session, principal, deadline_id)
    row.status = status
    if note:
        row.note = note
    row.confirmed_by = principal.user_id
    row.confirmed_at = datetime.now(timezone.utc)
    session.flush()
    return row


def add_deadline(session: Session, principal: Principal, case_id: int,
                 name: str, basis: str, due_on: date, fatal: bool = False,
                 note: str = "", today: Optional[date] = None) -> Deadline:
    """手工加一条规则覆盖不到的时限。"""
    _writable(session, principal, case_id)
    if not name.strip() or not basis.strip():
        raise DomainError("名称和依据都要填", code="task.deadline_incomplete")
    row = Deadline(
        tenant_id=principal.tenant_id, case_id=case_id,
        rule_code="manual.{0}".format(int(datetime.now(timezone.utc).timestamp())),
        name=name.strip()[:120], basis=basis.strip()[:255],
        anchor_field="", anchor_on=None, due_on=due_on,
        fatal=fatal, warn_days=30, note=note, source="manual",
        confirmed_by=principal.user_id, confirmed_at=datetime.now(timezone.utc),
    )
    row.status = _status_of(row, today or date.today())
    session.add(row)
    session.flush()
    return row


def _get(session: Session, principal: Principal, deadline_id: int) -> Deadline:
    row = session.scalar(select(Deadline).where(
        Deadline.id == deadline_id, Deadline.tenant_id == principal.tenant_id))
    if row is None:
        raise NotFound("时限不存在", code="task.deadline_not_found")
    _visible(session, principal, row.case_id)
    return row


# ================================================================ 任务


def create_task(session: Session, principal: Principal, title: str,
                case_id: Optional[int] = None, detail: str = "",
                assignee_id: Optional[int] = None, due_on: Optional[date] = None,
                priority: str = "normal", deadline_id: Optional[int] = None) -> Task:
    if not title.strip():
        raise DomainError("任务标题不能为空", code="task.title_required")
    if priority not in PRIORITIES:
        priority = "normal"
    _writable(session, principal, case_id)
    row = Task(
        tenant_id=principal.tenant_id, case_id=case_id, title=title.strip()[:255],
        detail=detail, assignee_id=assignee_id or principal.user_id,
        due_on=due_on, priority=priority, status="todo",
        deadline_id=deadline_id, created_by=principal.user_id,
    )
    session.add(row)
    session.flush()
    return row


def update_task(session: Session, principal: Principal, task_id: int, **kw) -> Task:
    row = _get_task(session, principal, task_id)
    if "status" in kw and kw["status"]:
        if kw["status"] not in TASK_STATUSES:
            raise DomainError("未知的任务状态", code="task.bad_status")
        row.status = kw["status"]
        if kw["status"] == "done":
            row.done_by = principal.user_id
            row.done_at = datetime.now(timezone.utc)
    for f in ("title", "detail", "priority"):
        if kw.get(f):
            setattr(row, f, kw[f])
    if "assignee_id" in kw:
        row.assignee_id = kw["assignee_id"]
    if "due_on" in kw:
        row.due_on = kw["due_on"]
    session.flush()
    return row


def _get_task(session: Session, principal: Principal, task_id: int) -> Task:
    row = session.scalar(select(Task).where(
        Task.id == task_id, Task.tenant_id == principal.tenant_id))
    if row is None:
        raise NotFound("任务不存在", code="task.not_found")
    _visible(session, principal, row.case_id)
    return row


def tasks_of_case(session: Session, principal: Principal, case_id: int) -> List[Task]:
    _visible(session, principal, case_id)
    return list(session.scalars(select(Task).where(
        Task.tenant_id == principal.tenant_id, Task.case_id == case_id
    ).order_by(Task.status, Task.due_on.nulls_last(), Task.id.desc())).all())


# ================================================================ 待办看板


def board(session: Session, principal: Principal, user_id: Optional[int] = None,
          today: Optional[date] = None) -> Dict[str, Any]:
    """我的待办：任务 + 时限，一起看。

    ★ 时限只列**我看得见的案件**上的 —— 数据范围和隔离墙照旧生效。
      为此要逐个案件问一遍 case 模块，慢一点无所谓，漏权限不行。
    """
    today = today or date.today()
    uid = user_id or principal.user_id

    tasks = list(session.scalars(select(Task).where(
        Task.tenant_id == principal.tenant_id,
        Task.assignee_id == uid,
        Task.status.in_(["todo", "doing"]),
    ).order_by(Task.due_on.nulls_last(), Task.id)).all())

    seen: Dict[int, bool] = {}

    def ok(case_id: Optional[int]) -> bool:
        if not case_id:
            return True
        if case_id not in seen:
            try:
                _case_api().assert_visible(session, principal, case_id)
                seen[case_id] = True
            except Exception:                      # noqa: BLE001
                seen[case_id] = False
        return seen[case_id]

    tasks = [t for t in tasks if ok(t.case_id)]

    rows = list(session.scalars(select(Deadline).where(
        Deadline.tenant_id == principal.tenant_id,
        Deadline.status.notin_(["done", "waived"]),
    )).all())
    for r in rows:
        r.status = _status_of(r, today)
    session.flush()
    dls = sort_deadlines([r for r in rows if ok(r.case_id)], today)

    return {
        "tasks": tasks,
        "deadlines": dls,
        "expired": len([d for d in dls if d.status == "expired"]),
        "urgent": len([d for d in dls if d.status == "urgent"]),
        "unknown": len([d for d in dls if d.status == "unknown"]),
        "overdue_tasks": len([t for t in tasks if t.due_on and t.due_on < today]),
    }


def describe(d: Deadline, today: Optional[date] = None) -> Dict[str, Any]:
    """给界面用的一条时限，**带人话说明**。"""
    today = today or date.today()
    left = (d.due_on - today).days if d.due_on else None
    if d.status == "unknown":
        msg = "缺「{0}」，算不出届满日 —— 这一项必须补齐".format(
            FIELD_LABELS.get(d.anchor_field, d.anchor_field or "起算日期"))
    elif d.status == "expired":
        msg = "已于 {0} 届满，超期 {1} 天。".format(d.due_on, -left)
        if d.fatal:
            msg += "**权利已丧失**，只能另寻救济。"
    elif d.status in ("ok", "urgent"):
        msg = "{0} 届满，还有 {1} 天。".format(d.due_on, left)

    elif d.status == "no_fixed":
        msg = "没有固定期间，算不出届满日 —— 需要个案判断，见下面的说明。"
    elif d.status == "done":
        msg = "已行使/已完成。"
    else:
        msg = "律师确认本案不适用。"
    # ★ 「已顶到最长保护期」这句话对**所有**状态都要说，尤其是已过期的。
    #   律师看到一条过期的诉讼时效，第一反应往往是「再发封催款函中断一下」——
    #   而这条已经没有中断的余地了。这时候不说，就是眼看着他去做无用功。
    if d.outer_due_on and d.due_on and d.due_on >= d.outer_due_on:
        msg += ("　★ 已顶到最长保护期（{0}）—— **再发生中断也不会往后延**，"
                "《民法典》188 条 2 款的 20 年不适用中断中止。"
                .format(d.outer_due_on))
    if d.suspend_end_on:
        msg += "　（已登记中止，事由消除日 {0}）".format(d.suspend_end_on)

    return {
        "id": d.id, "case_id": d.case_id, "rule_code": d.rule_code,
        "name": d.name, "basis": d.basis, "status": d.status, "fatal": d.fatal,
        "anchor_field": d.anchor_field,
        # ★ 手工录进来的时限没有「起算字段」这个概念 —— 律师直接填的届满日。
        #   这里如果照旧 fallback 到字段名，界面会渲染成「缺『』」，
        #   一个空括号看着像是数据坏了。没有就是没有，给空串。
        "anchor_label": FIELD_LABELS.get(d.anchor_field, d.anchor_field or ""),
        "anchor_on": d.anchor_on.isoformat() if d.anchor_on else "",
        "due_on": d.due_on.isoformat() if d.due_on else "",
        "days_left": left,
        "message": msg,
        # ★ 能不能中断由后端说了算，界面别自己按 fatal 猜 ——
        #   「没有固定期间」和「规则没标可中断」也一样不能中断
        "can_interrupt": (not d.fatal) and d.status not in ("no_fixed", "done", "waived"),
        "suspend_end_on": d.suspend_end_on.isoformat() if d.suspend_end_on else "",
        "suspend_note": d.suspend_note,
        "outer_due_on": d.outer_due_on.isoformat() if d.outer_due_on else "",
        # ★ 「已经顶到最长保护期」必须单独说。届满日和上限重合时，界面上
        #   看起来只是一个普通日期，但它的含义完全不同：**再中断也没用了**。
        "capped": bool(d.outer_due_on and d.due_on and d.due_on >= d.outer_due_on),
        "caveat": d.caveat,
        "note": d.note,
        "interrupted_on": d.interrupted_on.isoformat() if d.interrupted_on else "",
        "interrupt_note": d.interrupt_note,
        "confirmed": bool(d.confirmed_at),
        "source": d.source,
    }


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的时限与待办。"""
    _visible(session, principal, case_id)
    dls = deadlines_of(session, principal, case_id)
    tasks = tasks_of_case(session, principal, case_id)
    out = []
    if dls:
        expired = len([d for d in dls if d.status == "expired"])
        out.append({"name": "时限记录", "count": len(dls),
                    "note": "其中 {0} 条已过期".format(expired) if expired else ""})
    if tasks:
        out.append({"name": "待办事项", "count": len(tasks), "note": ""})
    return out


def archive_blockers(session, principal, case_id):
    """★ 还有没办完的待办。时限过期不拦 —— 那是已经发生的事实，拦也没用。"""
    _visible(session, principal, case_id)
    open_tasks = [t for t in tasks_of_case(session, principal, case_id)
                  if t.status in ("todo", "doing")]
    if open_tasks:
        return [{"what": "还有 {0} 条待办没完成".format(len(open_tasks)),
                 "how": "完成或取消这些待办"}]
    return []


# ================================================================ 看板指标


def metrics(session, principal, spec=None):
    """★ 用**存量**不用比率。

    「时限达成率 94%」一旦挂上墙，最快的提分办法是把时限标成「不适用」——
    而不是真的去盯。存量指向具体要办的事，比率只能被优化。
    """
    spec = spec or {}
    today = spec.get("today") or date.today()
    uid = spec.get("user_id")

    b = board(session, principal, uid, today)
    return {
        "task.expired": {"value": b["expired"], "text": "%d 条" % b["expired"]},
        "task.urgent": {"value": b["urgent"], "text": "%d 条" % b["urgent"]},
        "task.unknown": {"value": b["unknown"], "text": "%d 条" % b["unknown"]},
        "task.overdue": {"value": b["overdue_tasks"],
                         "text": "%d 条" % b["overdue_tasks"]},
    }


# ---------------------------------------------------------------- 统一待办

def inbox_items(session: Session, principal: Principal,
                today: Optional[date] = None) -> List[Dict[str, Any]]:
    """报进统一待办中心的条目：**未完成的时限 + 指派给我的任务**。

    ★ 「正常」的时限**也报**，但排在最后。
      律师需要看见"这个案子还有哪些期限在跑"，
      只报快出事的那些，会让人以为其余的不存在。

    ★ `no_fixed`（无固定期间）**不报** —— 它不是待办：
      没有任何动作能让它消失。报进来就是一条永远清不掉的记录，
      而清不掉的东西会让人放弃整个列表。
    """
    # ★★ **权限门在这里，不在汇总层。**
    #
    #   这条检查是漏掉之后补的：统一待办中心直接调 `board()`，
    #   而 board 本身不查权限（查权限的是路由）——
    #   于是**财务（没有 task.view）在待办中心里看到了 13 条案件时限**。
    #
    #   教训：**聚合层不该替被聚合的模块判断授权。**
    #   谁能看什么是各模块自己的事 —— 一旦交给汇总层去判，
    #   汇总层就要知道每个模块的权限模型，而它迟早会漏掉一个。
    #   （同一个错在 billing 拉工时时犯过一次：那次是反过来越权判了别人的。）
    if not principal.can("task.view"):
        return []

    today = today or date.today()
    b = board(session, principal, today=today)
    out: List[Dict[str, Any]] = []

    for d in b["deadlines"]:
        if d.status in ("no_fixed", "done", "waived"):
            continue
        out.append({
            "key": "task.deadline:{0}".format(d.id),
            "kind": "deadline",
            "title": d.name,
            "detail": d.basis or "",
            "due_on": d.due_on.isoformat() if d.due_on else "",
            "urgency": d.status if d.status in ("expired", "unknown", "urgent")
                       else "normal",
            "link": "/cases?id={0}".format(d.case_id),
            "case_id": d.case_id,
            # ★ 时限不挂人 —— 它跟着案子走，谁办案谁盯
            "why_me": "你经办的案件",
        })

    for t in b["tasks"]:
        overdue = bool(t.due_on and t.due_on < today)
        out.append({
            "key": "task.task:{0}".format(t.id),
            "kind": "task",
            "title": t.title,
            "detail": t.detail or "",
            "due_on": t.due_on.isoformat() if t.due_on else "",
            "urgency": "expired" if overdue else "normal",
            "link": "/cases?id={0}".format(t.case_id) if t.case_id else "/todo",
            "case_id": t.case_id,
            "why_me": "指派给你",
        })
    return out
