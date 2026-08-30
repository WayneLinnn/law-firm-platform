# -*- coding: utf-8 -*-
"""归档。

**归档 = 三件事，缺一件都不算归了：**

    ① 拦住新的写入      —— 由 case.assert_writable 管（不在本模块）
    ② 编一份卷宗目录     —— 问遍所有模块「你这有几件」
    ③ 算一个指纹存下来   —— 将来能回答「这卷被动过吗」

★ 归档**之前**要先检查有没有没了结的事（`blockers`）。

  这一条是设计上最要紧的：如果允许"先归档，再慢慢把剩下的账结完"，
  就必须允许归档后写入 —— 而那样锁就形同虚设。
  **反过来做才对：钱和活都清了才让归档。** 于是锁可以是硬的。

★ 归档必须能解档。案子会再审、会申请执行。
  没有解档，归档就是死胡同，然后大家都不敢归档了 ——
  一个没人敢用的功能等于没做。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.archive.models import ArchiveRecord

logger = logging.getLogger(__name__)

#: 默认保存年限。★ 律所自定，各地司法行政要求不同 —— 所以是个默认值不是硬规定
DEFAULT_KEEP_YEARS = 10

#: 会被问「你这有几件」的模块，按卷宗里的自然顺序排。
#:
#: ★ 顺序是有讲究的：卷宗目录不是随便列，是按办案顺序 ——
#:   先有委托和当事人，然后是利冲、材料、检索、文书，最后才是钱。
#:   律师翻卷是按这个顺序翻的。
ASK_ORDER = ("case", "conflict", "doc", "retrieval", "draft",
             "workflow", "task", "consult", "billing", "approval")


def _case_api():
    return registry.api("case")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ask(module: str, fn: str, *args, **kw):
    """问某个模块要东西。**模块没装、或者没实现这个函数，就跳过。**

    ★ 跳过要留日志。一个模块悄悄不参与归档，卷就少了一部分，
      而少了的那部分没人会发现 —— 目录上根本不会出现它。
    """
    api = registry.optional_api(module)
    if api is None:
        return None
    f = getattr(api, fn, None)
    if f is None:
        logger.info("模块 %s 没实现 %s，归档时跳过", module, fn)
        return None
    try:
        return f(*args, **kw)
    except Exception as exc:                             # noqa: BLE001
        logger.warning("问 %s.%s 失败：%s", module, fn, exc)
        return None


# ================================================================ 目录


def inventory(session: Session, principal: Principal,
              case_id: int) -> Dict[str, Any]:
    """编一份卷宗目录：问遍所有模块「你这有几件」。

    ★ **不改任何东西**，随时可以看。归档前先看一眼这份目录，
      是律师确认"该进卷的都进了"的唯一办法。
    """
    _case_api().assert_visible(session, principal, case_id)

    groups: List[Dict[str, Any]] = []
    total = 0
    skipped: List[str] = []
    for mod in ASK_ORDER:
        rows = _ask(mod, "archive_inventory", session, principal, case_id)
        if rows is None:
            api = registry.optional_api(mod)
            if api is not None:
                skipped.append(mod)
            continue
        for r in rows:
            n = int(r.get("count") or 0)
            groups.append({"module": mod, "name": r.get("name") or mod,
                           "count": n, "note": r.get("note", "")})
            total += n
    return {
        "case_id": case_id,
        "groups": groups,
        "item_count": total,
        #: ★ 哪些模块装着却没报数 —— 明说，别让卷悄悄缺一块
        "skipped": skipped,
    }


def blockers(session: Session, principal: Principal,
             case_id: int) -> List[Dict[str, str]]:
    """归档前的阻断检查：还有什么没了结。

    ★ 顺序是「钱和活都清了才让归档」，不是「先归档再慢慢清」。
      后者必然要求归档后可写，那锁就形同虚设了。
    """
    _case_api().assert_visible(session, principal, case_id)
    out: List[Dict[str, str]] = []
    for mod in ASK_ORDER:
        rows = _ask(mod, "archive_blockers", session, principal, case_id)
        for r in (rows or []):
            out.append({"module": mod,
                        "what": r.get("what", ""),
                        "how": r.get("how", "")})
    return out


def _fingerprint(inv: Dict[str, Any]) -> str:
    """目录指纹。

    ★ 只按 (模块, 名称, 件数) 算 —— 不含时间戳、不含 note。
      算进易变的东西，指纹每次都不一样，就验不出"被动过"。
    """
    body = "|".join("{0}:{1}:{2}".format(g["module"], g["name"], g["count"])
                    for g in inv["groups"])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


# ================================================================ 归档


def _next_volume_no(session: Session, tenant_id: int, today: date) -> str:
    prefix = "J{0}".format(today.year)
    n = session.scalar(select(func.count()).select_from(ArchiveRecord).where(
        ArchiveRecord.tenant_id == tenant_id,
        ArchiveRecord.volume_no.like(prefix + "%"))) or 0
    return "{0}-{1:04d}".format(prefix, n + 1)


def current(session: Session, principal: Principal,
            case_id: int) -> Optional[ArchiveRecord]:
    return session.scalar(select(ArchiveRecord).where(
        ArchiveRecord.tenant_id == principal.tenant_id,
        ArchiveRecord.case_id == case_id,
        ArchiveRecord.active.is_(True)))


def archive(session: Session, principal: Principal, case_id: int,
            note: str = "", keep_years: int = DEFAULT_KEEP_YEARS,
            force: bool = False, today: Optional[date] = None) -> ArchiveRecord:
    """归档：编目录 → 存指纹 → 把案件状态推到 archived。

    ★ `force` 只有管委会能用，而且**阻断项会原样记进 note** ——
      带着未结清账单归档是所里的决定，但那个决定要留痕。
    """
    if not principal.can("archive.do"):
        raise DomainError("归档需要 archive.do 权限", code="archive.forbidden")
    today = today or date.today()

    case = _case_api().assert_visible(session, principal, case_id)
    if current(session, principal, case_id) is not None:
        raise DomainError("这个案子已经归过档了", code="archive.already")

    # ★ 硬前提，**force 也绕不过去**：只有「已结案」能归档。
    #   这不是"可以强制跳过的事"，是状态机的规矩 ——
    #   放进 blockers 让 force 跳过，只会建出一条孤儿归档记录，
    #   然后卡在 transition 上。
    if case.status != "closed":
        raise DomainError(
            "只有「已结案」的案件能归档，这个案子现在是「{0}」。\n"
            "先把案子办完、结案，再归档。".format(
                _case_api().STATUS_LABELS.get(case.status, case.status)),
            code="archive.not_closed")

    stuck = blockers(session, principal, case_id)
    if stuck and not force:
        raise DomainError(
            "还不能归档，有 {0} 件事没了结：\n{1}\n"
            "★ 归档的前提是钱和活都清了 —— 归了档卷就定死，"
            "再想补东西只能解档。".format(
                len(stuck),
                "\n".join("· {0}（{1}）".format(x["what"], x["how"])
                          for x in stuck)),
            code="archive.blocked")
    if stuck and force:
        if not principal.can("archive.force"):
            raise DomainError("带阻断项强制归档需要 archive.force 权限",
                              code="archive.forbidden")
        note = ("【强制归档，未了结：{0}】{1}".format(
            "；".join(x["what"] for x in stuck), note))[:255]

    # ★ **先推状态，再编目录。** 顺序反了指纹就永远对不上 ——
    #   因为「推到已归档」这个动作**自己会往状态流转记录里加一条**。
    #   先编目录的话，目录里少了那一条，verify() 每次都报「被动过」，
    #   而一个永远报警的校验，两天之内就会被人无视。
    #
    #   （原来这里写着"最后才推状态，否则 inventory 会撞上锁" —— 那是想多了：
    #     inventory 是只读的，锁只拦写。）
    if case.status != "archived":
        _case_api().transition(session, case_id, "archived",
                               actor_id=principal.user_id,
                               note="归档")

    inv = inventory(session, principal, case_id)
    row = ArchiveRecord(
        tenant_id=principal.tenant_id, case_id=case_id,
        volume_no=_next_volume_no(session, principal.tenant_id, today),
        inventory=json.dumps(inv["groups"], ensure_ascii=False),
        item_count=inv["item_count"],
        fingerprint=_fingerprint(inv),
        archived_by=principal.user_id,
        keep_until=date(today.year + keep_years, today.month, today.day)
                   if keep_years else None,
        note=note[:255],
    )
    session.add(row)
    session.flush()
    return row


def unarchive(session: Session, principal: Principal, case_id: int,
              reason: str, to_status: str = "closed") -> ArchiveRecord:
    """解档。案子要再审、要申请执行，就得能解开。

    ★ 必须写理由，而且**原记录不删** —— 归过档就是归过档，
      留着才说得清"这卷被解开过一次，因为什么"。
    """
    if not principal.can("archive.unarchive"):
        raise DomainError("解档需要 archive.unarchive 权限",
                          code="archive.forbidden")
    if not reason.strip():
        raise DomainError("解档必须写明理由 —— 解了档没人说得清为什么，"
                          "等于没归过", code="archive.reason_required")

    row = current(session, principal, case_id)
    if row is None:
        raise NotFound("这个案子没有生效的归档记录", code="archive.not_archived")

    row.active = False
    row.unarchived_by = principal.user_id
    row.unarchived_at = _now()
    row.unarchive_reason = reason.strip()[:255]
    session.flush()

    _case_api().transition(session, case_id, to_status,
                           actor_id=principal.user_id,
                           note="解档：{0}".format(reason.strip()[:80]))
    return row


def verify(session: Session, principal: Principal,
           case_id: int) -> Dict[str, Any]:
    """★ 这卷被动过吗？拿现在的目录重算指纹，和归档时的比。

    正常情况下**永远相同** —— 因为归档后写不进去。
    不同就说明有人绕过了锁（直接改库、或者某个模块漏了检查）。
    这是一道**兜底的体检**，不是日常功能。
    """
    row = current(session, principal, case_id)
    if row is None:
        raise NotFound("这个案子没有生效的归档记录", code="archive.not_archived")
    now = inventory(session, principal, case_id)
    fp = _fingerprint(now)
    return {
        "volume_no": row.volume_no,
        "ok": fp == row.fingerprint,
        "fingerprint_then": row.fingerprint,
        "fingerprint_now": fp,
        "item_count_then": row.item_count,
        "item_count_now": now["item_count"],
        "message": "卷与归档时一致" if fp == row.fingerprint else
                   "★ 卷和归档时不一样了 —— 有人绕过了归档锁，请查审计日志",
    }


def describe(r: ArchiveRecord) -> Dict[str, Any]:
    return {
        "id": r.id, "case_id": r.case_id, "volume_no": r.volume_no,
        "groups": json.loads(r.inventory or "[]"),
        "item_count": r.item_count,
        "fingerprint": r.fingerprint,
        "archived_by": r.archived_by,
        "archived_at": r.archived_at.isoformat() if r.archived_at else "",
        "keep_until": r.keep_until.isoformat() if r.keep_until else "",
        "note": r.note,
        "active": r.active,
        "unarchived_by": r.unarchived_by,
        "unarchived_at": r.unarchived_at.isoformat() if r.unarchived_at else "",
        "unarchive_reason": r.unarchive_reason,
    }


def history(session: Session, principal: Principal,
            case_id: int) -> List[ArchiveRecord]:
    """归档/解档的全部历史 —— 解档过几次，各是为什么。"""
    _case_api().assert_visible(session, principal, case_id)
    return list(session.scalars(select(ArchiveRecord).where(
        ArchiveRecord.tenant_id == principal.tenant_id,
        ArchiveRecord.case_id == case_id).order_by(ArchiveRecord.id)).all())


def tab_counts(session: Session, principal: Principal,
               case_id: int) -> Dict[str, int]:
    """案件页每个**页签**上该显示几 —— 给前端用。

    ★ **直接复用 `inventory()`**，不另写一套计数。
      两套计数迟早会漂：页签上写 3、点进去 5，
      然后用户不再相信任何一个数字。

    ★ 返回的键是**页签名**不是模块名 —— 一个页签可能装好几个模块
      （「财务」里同时有 consult 和 billing）。这一层映射放在这里，
      因为它是**界面的分组决定**，不该让前端去猜。
    """
    inv = inventory(session, principal, case_id)

    #: 页签 → 哪些模块的数加起来
    TABS = {
        "work": ("conflict", "workflow"),
        "draft": ("draft",),
        "deadline": ("task",),
        "doc": ("doc",),
        "money": ("consult", "billing"),
        "archive": (),          # 卷宗页签显示总数
    }
    per_module: Dict[str, int] = {}
    for g in inv["groups"]:
        m = g["module"]
        per_module[m] = per_module.get(m, 0) + int(g["count"] or 0)

    out: Dict[str, int] = {}
    for tab, mods in TABS.items():
        n = sum(per_module.get(m, 0) for m in mods)
        if n:
            out[tab] = n
    if inv["item_count"]:
        out["archive"] = inv["item_count"]
    return out
