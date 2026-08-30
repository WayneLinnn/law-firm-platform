"""利冲检查的判定逻辑。

检查回答两个方向的问题：

    对方 → 我方   "这个对家，是不是我们的客户？或者是我们客户的关联方？"
    我方 → 对方   "我们要代理的这一方，所里是不是已经有人在告他？"

两个方向都要查，而且都要顺着**关联方**展开一层 ——
只比名字的话，"对方是我们客户的全资子公司"这种典型利冲根本查不出来。

判定分三档：

    blocked  对方就是我们在办案件的当事人 —— 不能接
    review   关联方冲突、所内另一团队在代理对方 —— 管委会决定，通常配隔离墙
    notice   已结案的旧关系 —— 记录即可

**只有 blocked 是自动结论，review 一律要人来判。** 利冲的判断依赖商业背景
（是不是同一实控人、业务是否实质关联），机器给不出确定答案，只能把线索
摆全了交给管委会。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from modules.conflict.models import (
    CHECK_STATUSES,
    HIT_KINDS,
    HIT_LEVELS,
    PASSING_STATUSES,
    ConflictCheck,
    ConflictHit,
)

#: 关联方展开层数。1 层 = 母子公司、法定代表人这类直接关系。
#: 再深会把整个商业网络拉进来，命中全是误报，人工根本看不过来。
RELATION_DEPTH = 1

LEVEL_ORDER = {"notice": 0, "review": 1, "blocked": 2}


# ---------------------------------------------------------------- 执行检查


def run_check(
    session: Session,
    tenant_id: int,
    case_id: int,
    requested_by: Optional[int] = None,
) -> ConflictCheck:
    """跑一次利冲检查并留档。

    同一个案件可以查多次（当事人补录后要重查），每次留一条记录 ——
    历史记录不覆盖，事后要能看到"当时查的是什么、查出了什么"。
    """
    case_api = registry.api("case")
    crm = registry.api("crm")
    party_names = _party_names(session, crm, tenant_id)

    case = case_api.get_case(session, case_id)
    if case.tenant_id != tenant_id:
        raise DomainError("案件不属于本律所", code="conflict.cross_tenant")

    parties = case_api.parties_of(session, case_id)
    if not parties:
        raise DomainError(
            "本案还没有登记当事人，无法做利冲检查",
            code="conflict.no_parties",
        )

    check = ConflictCheck(
        tenant_id=tenant_id, case_id=case_id, status="pending",
        requested_by=requested_by,
        snapshot=json.dumps(
            [{"party_id": p.party_id, "name": party_names.get(p.party_id, ""),
              "role": p.role, "is_ours": p.is_ours} for p in parties],
            ensure_ascii=False,
        ),
    )
    session.add(check)
    session.flush()

    hits: List[ConflictHit] = []
    for party in parties:
        if party.is_ours:
            hits.extend(_check_our_side(session, tenant_id, case_id, party.party_id,
                                        check.id, case_api, crm, party_names))
        else:
            hits.extend(_check_their_side(session, tenant_id, case_id, party.party_id,
                                          check.id, case_api, crm, party_names))

    for hit in hits:
        session.add(hit)
    session.flush()

    check.status = _conclude(hits)
    if check.status == "cleared":
        check.decided_at = datetime.now(timezone.utc)
    session.flush()
    # EVENT: conflict.checked
    return check


def _party_names(session: Session, crm, tenant_id: int) -> Dict[int, str]:
    return {p.id: p.name for p in crm.search_parties(session, tenant_id, limit=10000)}


def _check_their_side(
    session, tenant_id, case_id, party_id, check_id, case_api, crm, names,
) -> List[ConflictHit]:
    """对方 → 我方：这个对家是不是我们的人？"""
    out: List[ConflictHit] = []
    expanded = crm.related_party_ids(session, party_id, depth=RELATION_DEPTH)
    subject = names.get(party_id, "主体%d" % party_id)

    # 1) 对方（或其关联方）是我们的客户
    for pid, client in crm.clients_by_parties(session, tenant_id, expanded).items():
        direct = pid == party_id
        out.append(ConflictHit(
            check_id=check_id,
            level="blocked" if direct else "review",
            kind="direct" if direct else "affiliate",
            subject_party_id=party_id, matched_party_id=pid,
            related_client_id=client.id,
            description=(
                "本案对方「{0}」就是我所客户（{1}）".format(subject, client.code)
                if direct else
                "本案对方「{0}」的关联方「{1}」是我所客户（{2}）".format(
                    subject, names.get(pid, pid), client.code)
            ),
        ))

    # 2) 对方（或其关联方）在所里别的案子里是"我方当事人"
    for cp in case_api.cases_of_party(session, tenant_id, expanded, ours=True):
        if cp.case_id == case_id:
            continue
        other = case_api.get_case(session, cp.case_id)
        direct = cp.party_id == party_id
        open_case = other.status in case_api.OPEN_STATUSES
        out.append(ConflictHit(
            check_id=check_id,
            level="blocked" if (direct and open_case) else ("review" if open_case else "notice"),
            kind="direct" if direct else ("affiliate" if open_case else "historical"),
            subject_party_id=party_id, matched_party_id=cp.party_id,
            related_case_id=cp.case_id,
            description="本案对方「{0}」{1}在案件 {2}（{3}）中是我方当事人".format(
                subject,
                "" if direct else "的关联方「{0}」".format(names.get(cp.party_id, cp.party_id)),
                other.code, case_api.STATUS_LABELS.get(other.status, other.status),
            ),
        ))
    return out


def _check_our_side(
    session, tenant_id, case_id, party_id, check_id, case_api, crm, names,
) -> List[ConflictHit]:
    """我方 → 对方：所里是不是已经有人在告我们要代理的这一方？"""
    out: List[ConflictHit] = []
    expanded = crm.related_party_ids(session, party_id, depth=RELATION_DEPTH)
    subject = names.get(party_id, "主体%d" % party_id)

    for cp in case_api.cases_of_party(session, tenant_id, expanded, ours=False):
        if cp.case_id == case_id:
            continue
        other = case_api.get_case(session, cp.case_id)
        open_case = other.status in case_api.OPEN_STATUSES
        direct = cp.party_id == party_id
        out.append(ConflictHit(
            check_id=check_id,
            level="review" if open_case else "notice",
            kind="firm_side" if open_case else "historical",
            subject_party_id=party_id, matched_party_id=cp.party_id,
            related_case_id=cp.case_id,
            description="我方「{0}」{1}在案件 {2}（{3}）中是对方当事人，所内已有团队站在对面".format(
                subject,
                "" if direct else "的关联方「{0}」".format(names.get(cp.party_id, cp.party_id)),
                other.code, case_api.STATUS_LABELS.get(other.status, other.status),
            ),
        ))
    return out


def _conclude(hits: Sequence[ConflictHit]) -> str:
    """有 blocked 就阻断；有 review 就等人判；其余放行。

    **notice 不阻断** —— 已结案的旧关系记录即可，否则老所几乎接不了新案子。
    """
    if any(h.level == "blocked" for h in hits):
        return "blocked"
    if any(h.level == "review" for h in hits):
        return "pending"
    return "cleared"


# ---------------------------------------------------------------- 处置


def decide(
    session: Session,
    check_id: int,
    status: str,
    decided_by: int,
    note: str,
    walls: Optional[Sequence[Dict[str, Any]]] = None,
) -> ConflictCheck:
    """管委会处置。

    status=waived   放行。**必须写理由** —— 这是事后唯一能解释"为什么当时接了"的东西
    status=blocked  阻断，案子不能接

    walls：放行时顺带设隔离墙，形如
        [{"user_id": 12, "reason": "利冲隔离"}]
    隔离墙优先级高于一切数据范围，管委会主任自己也会被挡住。
    """
    if status not in ("waived", "blocked"):
        raise DomainError("处置结论只能是 waived 或 blocked", code="conflict.bad_decision")
    check = get_check(session, check_id)
    if check.status in PASSING_STATUSES and check.status == "cleared":
        raise DomainError("本次检查无冲突，无需处置", code="conflict.nothing_to_decide")
    if not note.strip():
        raise DomainError(
            "放行或阻断必须写明理由 —— 事后追责要靠它",
            code="conflict.note_required",
        )

    check.status = status
    check.decided_by = decided_by
    check.decided_at = datetime.now(timezone.utc)
    check.decision_note = note.strip()

    for hit in hits_of(session, check_id):
        if not hit.resolved:
            hit.resolved = True
            hit.resolution = note.strip()

    if status == "waived" and walls:
        identity = registry.api("identity")
        case_api = registry.api("case")
        case = case_api.get_case(session, check.case_id)
        for wall in walls:
            identity.set_wall(
                session, check.tenant_id, "case", str(case.id),
                int(wall["user_id"]), mode=wall.get("mode", "deny"),
                reason=wall.get("reason", "利冲隔离"), created_by=decided_by,
            )
    session.flush()
    # EVENT: conflict.decided
    return check


def resolve_hit(session: Session, hit_id: int, resolution: str) -> ConflictHit:
    """单条处置。逐条给理由，比整体一句"已确认无冲突"更经得起检查。"""
    hit = session.get(ConflictHit, hit_id)
    if hit is None:
        raise NotFound("命中记录 {0} 不存在".format(hit_id))
    if not resolution.strip():
        raise DomainError("处置意见不能为空", code="conflict.resolution_required")
    hit.resolution = resolution.strip()
    hit.resolved = True
    session.flush()
    return hit


# ---------------------------------------------------------------- 查询


def get_check(session: Session, check_id: int) -> ConflictCheck:
    check = session.get(ConflictCheck, check_id)
    if check is None:
        raise NotFound("利冲检查 {0} 不存在".format(check_id))
    return check


def hits_of(session: Session, check_id: int) -> List[ConflictHit]:
    rows = list(session.scalars(
        select(ConflictHit).where(ConflictHit.check_id == check_id)
    ))
    return sorted(rows, key=lambda h: (-LEVEL_ORDER.get(h.level, 0), h.id))


def checks_of_case(session: Session, case_id: int) -> List[ConflictCheck]:
    """一个案件的全部检查记录，最新在前。**历史不覆盖。**"""
    return list(session.scalars(
        select(ConflictCheck)
        .where(ConflictCheck.case_id == case_id)
        .order_by(ConflictCheck.id.desc())
    ))


def latest_check(session: Session, case_id: int) -> Optional[ConflictCheck]:
    checks = checks_of_case(session, case_id)
    return checks[0] if checks else None


def is_cleared(session: Session, case_id: int) -> bool:
    """本案的利冲是不是过了。case 模块推进到「待立案」前会问这个。"""
    check = latest_check(session, case_id)
    return check is not None and check.status in PASSING_STATUSES


def assert_cleared(session: Session, case_id: int) -> None:
    """没过利冲就不许往下走。**这是执业红线，不能靠人记。**"""
    check = latest_check(session, case_id)
    if check is None:
        raise DomainError(
            "本案尚未做利冲检查，不能进入下一步",
            code="conflict.not_checked",
        )
    if check.status == "blocked":
        raise DomainError(
            "本案利冲检查为阻断，不能承接",
            code="conflict.blocked",
        )
    if check.status not in PASSING_STATUSES:
        pending = [h for h in hits_of(session, check.id) if not h.resolved]
        raise DomainError(
            "本案利冲有 {0} 条命中未处置，需管委会审批后才能继续".format(len(pending)),
            code="conflict.pending",
        )


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的利冲记录。★ 这一项是**必须进卷**的合规材料。"""
    registry.api("case").assert_visible(session, principal, case_id)
    checks = checks_of_case(session, case_id)
    if not checks:
        return []
    return [{"name": "利冲检查记录", "count": len(checks), "note": ""}]
