# -*- coding: utf-8 -*-
"""看板：只汇总，不碰任何别人的表。

★ **各模块自报指标，本模块只做三件事**：遍历、按权限筛、拼版面。

  为什么反过来做（不是 analytics 去依赖所有模块）：依赖方向。
  让 analytics 依赖案件、账单、时限、单据，加一个模块就要改一次 analytics；
  反过来 —— **加一个模块 = 看板自动多几个指标，analytics 一行不改。**

  这是本项目第四次用这个模式（capabilities / archive_inventory /
  archive_blockers / metrics），已经是固定手法。

★ **不做工时和计费率。** 用户明确：不计工时，记创收。
  计费率那套指标是英美计时制律所的，照搬过来只会得到一堆没人看的数字。

★ 创收有三个口径，界面上必须分清 —— 混了就有人拿开票额去邀功：

      本期开票   开了账单，不代表收到
      本期回款   钱到账了 —— **律所说"创收"通常指这个**
      未收       开了收不回来的
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from kernel.errors import DomainError
from kernel.registry import registry
from kernel.security import Principal

logger = logging.getLogger(__name__)


def _ask(module: str, fn: str, *args, **kw):
    """问某个模块要东西。没装、没实现、报错，都跳过并留日志。"""
    api = registry.optional_api(module)
    if api is None:
        return None
    f = getattr(api, fn, None)
    if f is None:
        return None
    try:
        return f(*args, **kw)
    except Exception as exc:                             # noqa: BLE001
        logger.warning("问 %s.%s 失败：%s", module, fn, exc)
        return None


def catalog(principal: Principal,
            scope: str = "", org_id: Optional[int] = None,
            since: Optional[date] = None,
            until: Optional[date] = None) -> List[Dict[str, Any]]:
    """看板上**我看得到**的指标有哪些。前端据此渲染，不写死。

    ★ **范围是在这里拼进 drill 的，不在模块里。**
      模块只声明"这个数点开去 /todo?f=expired"；
      至于点开之后该看谁的、看哪段时间，模块不知道也不该知道 ——
      同一个 `case.open`，工作台上是"我经办的"，全所页上是"全所的"。

    ★ 不拼范围会出一个很难查的错：工作台显示"在办 4 件"，
      点进去是全所 11 件。**数字和它点开的东西对不上，
      整个看板就不可信了** —— 用户会以为是统计算错了。
    """
    extra = []
    if scope:
        extra.append("scope=" + scope)
    if org_id:
        extra.append("org=%d" % org_id)
    if since:
        extra.append("since=" + since.isoformat())
    if until:
        extra.append("until=" + until.isoformat())
    tail = "&".join(extra)

    out = []
    for m in registry.metrics():
        if m.scope_perm and not principal.can(m.scope_perm):
            continue
        drill = m.drill
        if drill and tail:
            drill += ("&" if "?" in drill else "?") + tail
        out.append({"key": m.key, "name": m.name, "unit": m.unit,
                    "description": m.description, "drill": drill})
    return out


def month_range(today: Optional[date] = None) -> Dict[str, date]:
    """默认口径是**本月**，不是"最近 30 天"。

    ★ 律所是按月结算的 —— 「最近 30 天」跨两个结算月，
      看到的数对不上任何一张月报表。
    """
    today = today or date.today()
    return {"since": today.replace(day=1), "until": today}


def collect(session: Session, principal: Principal,
            spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把所有模块的指标收上来。

    ★ 拿不到的**明说**（`missing`），不是当成 0。
      一个模块挂了却显示 0，看板上就是"本月回款 0 元" —— 那会吓死人，
      而且方向反了：真实情况可能是收得很好，只是这个模块没响应。
    """
    spec = dict(spec or {})
    spec.setdefault("today", date.today())

    values: Dict[str, Any] = {}
    missing: List[str] = []
    for m in registry.metrics():
        if m.scope_perm and not principal.can(m.scope_perm):
            continue
        mod = m.key.split(".", 1)[0]
        if mod in values.get("__asked__", ()):           # 每个模块只问一次
            continue
        got = _ask(mod, "metrics", session, principal, spec)
        values.setdefault("__asked__", set()).add(mod)
        if got is None:
            missing.append(mod)
            continue
        values.update(got)
    values.pop("__asked__", None)

    return {
        "since": spec.get("since").isoformat() if spec.get("since") else "",
        "until": spec.get("until").isoformat() if spec.get("until") else "",
        "values": values,
        #: ★ 哪些模块没报上来 —— 界面要标灰并说"查不到"，不能显示 0
        "missing": missing,
    }


def overview(session: Session, principal: Principal,
             scope: str = "mine", org_id: Optional[int] = None,
             since: Optional[date] = None, until: Optional[date] = None,
             today: Optional[date] = None) -> Dict[str, Any]:
    """一个视角的完整看板数据。

    scope：
        mine  我的      —— 只看我主办的
        team  团队的    —— 要 org_id
        firm  全所的
    """
    if not principal.can("analytics.view"):
        raise DomainError("看板需要 analytics.view 权限",
                          code="analytics.forbidden")
    if scope == "firm" and not principal.can("analytics.view_firm"):
        raise DomainError("全所看板需要 analytics.view_firm 权限",
                          code="analytics.forbidden")

    today = today or date.today()
    rng = month_range(today)
    spec: Dict[str, Any] = {
        "since": since or rng["since"],
        "until": until or rng["until"],
        "today": today,
    }
    if scope == "mine":
        spec["user_id"] = principal.user_id
    elif scope == "team":
        if not org_id:
            raise DomainError("看团队要指定哪个团队", code="analytics.org_required")
        spec["org_id"] = org_id

    out = collect(session, principal, spec)
    out["scope"] = scope
    out["catalog"] = catalog(principal, scope=scope, org_id=org_id,
                             since=spec["since"], until=spec["until"])

    # 创收明细：按人、按案件。★ 只在团队/全所视角给
    if scope in ("team", "firm") and principal.can("billing.view"):
        out["by_lead"] = _ask("billing", "revenue_by_lead",
                              session, principal, spec) or {}
        cases = _ask("billing", "case_revenue", session, principal, spec) or []
        # 只给"收不回来的"前几个 —— 列表要指向行动，不是给人翻账
        out["at_risk"] = [c for c in cases
                          if float(c["outstanding"]) > 0][:10]
    return out


# ---------------------------------------------------------------- 统一待办中心

#: 排序权重。★ **顺序本身是个业务判断，不是实现细节。**
#:
#:   `unknown`（算不出）排在 `urgent`（临近）**前面** ——
#:   临近的期限至少系统在替你盯着；算不出的那些**没人在盯**。
#:   一个看起来干净的待办列表如果藏着三条算不出的除斥期间，它就是在骗人。
_URGENCY_ORDER = {"expired": 0, "unknown": 1, "urgent": 2, "normal": 3}

URGENCY_LABEL = {
    "expired": "已过期", "unknown": "算不出",
    "urgent": "临近", "normal": "正常",
}

KIND_LABEL = {"deadline": "时限", "task": "任务", "ticket": "单据"}


def inbox(session: Session, principal: Principal,
          today: Optional[date] = None) -> Dict[str, Any]:
    """统一待办中心：**所有等我动手的事，一个列表。**

    ★ 为什么要有这个东西：待办原来散在三个地方 —— 待办页有时限、
      单据页有「待我处理」、工作台还有一排格子。
      律师早上要点三个地方才知道今天有什么会出事，**于是就不点了**。

    ★ 和看板一样：**各模块自报，这里只汇总**。
      加一个模块 = 待办中心自动多一类事，这个函数一行不改。

    ★ 某个模块炸了要**明说**（`missing`），不能当成"那边没事"。
      一个漏掉了单据的待办列表，比没有列表更危险 ——
      它让人以为自己已经看全了。
    """
    today = today or date.today()
    items: List[Dict[str, Any]] = []
    missing: List[str] = []

    for name in registry.inbox_modules():
        try:
            api = registry.optional_api(name)
            if api is None or not hasattr(api, "inbox_items"):
                continue
            got = api.inbox_items(session, principal, today=today) or []
            items.extend(got)
        except Exception as exc:                     # noqa: BLE001
            logger.warning("待办中心：模块 %s 没报上来：%s", name, exc)
            missing.append(name)

    # ★ 先按紧急度，再按到期日。**算不出的排在临近前面**，见上面的注释。
    def _sort_key(x):
        u = _URGENCY_ORDER.get(x.get("urgency"), 9)
        due = x.get("due_on") or "9999-12-31"
        return (u, due, x.get("title", ""))

    items.sort(key=_sort_key)

    by_urgency: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for x in items:
        u = x.get("urgency", "normal")
        by_urgency[u] = by_urgency.get(u, 0) + 1
        k = x.get("kind", "task")
        by_kind[k] = by_kind.get(k, 0) + 1

    return {
        "items": items,
        "total": len(items),
        "by_urgency": by_urgency,
        "by_kind": by_kind,
        #: ★ 哪个模块没报上来。界面必须显示 —— 见上
        "missing": missing,
        "asked": registry.inbox_modules(),
    }
