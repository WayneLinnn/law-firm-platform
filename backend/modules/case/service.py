"""案件与事项的业务逻辑。

三件事决定了这个模块的价值：

  1. **状态机** —— 合法流转写死，非法流转报错。已结案的案子不能再回到
     办理中，利冲没过不能直接开始办 —— 这些约束必须在代码里，不能靠人记。

  2. **当事人登记** —— 我方/对方指向 crm 主体库。Step 7 的利冲查的就是它。

  3. **主办律师资格** —— 律师助理不能独立承办（Step 3 的职级开关驱动）。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from kernel.container import container
from kernel.errors import DomainError, NotFound
from kernel.registry import registry
import logging

from modules.case.models import (
    CASE_CATEGORIES,
    CASE_MEMBER_ROLES,
    CASE_STATUSES,
    MATTER_KINDS,
    MATTER_STATUSES,
    PARTY_ROLES,
    Case,
    CaseMember,
    CaseParty,
    CaseStatusLog,
    CauseOfAction,
    Matter,
)

logger = logging.getLogger(__name__)

# ================================================================ 状态机


#: **案件状态只能这样流转。** 写在这里而不是散在各处的 if。
#:
#: draft ─→ conflict_check ─→ pending ─→ active ─→ closing ─→ closed ─→ archived
#:   └──────────────────────── terminated ←──────────────────┘
ALLOWED_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "draft": ("conflict_check", "terminated"),
    "conflict_check": ("pending", "draft", "terminated"),
    "pending": ("active", "conflict_check", "terminated"),
    "active": ("closing", "terminated"),
    "closing": ("closed", "active"),
    "closed": ("archived", "closing"),
    # ★ 归档**不是终点**。案子会再审、会申请执行 —— 解档回到「已结案」。
    #   原来这里是空元组，于是解档在状态机上根本走不通：
    #   archive 模块写了 unarchive()，测试一跑才发现它调 transition 必然失败。
    #   **一个走不通的解档，等于归档是个死胡同，然后没人敢归档。**
    "archived": ("closed",),
    "terminated": (),
}

#: 这些状态下案件视为"在办"，会出现在待办、计时、时限里
OPEN_STATUSES = ("pending", "active", "closing")

STATUS_LABELS = {
    "draft": "草稿", "conflict_check": "利冲审查中", "pending": "待立案",
    "active": "办理中", "closing": "结案中", "closed": "已结案",
    "archived": "已归档", "terminated": "已终止",
}


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in ALLOWED_TRANSITIONS.get(from_status, ())


# ================================================================ 案由字典


def create_cause(
    session: Session,
    tenant_id: int,
    code: str,
    name: str,
    category: str = "civil",
    parent_id: Optional[int] = None,
    workflow_template: str = "",
    sort_order: int = 0,
    #: ★ 内置案由灌种子时用，放在参数表**最后** —— 私有参数插在
    #:   公开参数中间，会让按位置传参的调用方莫名其妙地错位。
    #:   内置值是**代码**不是用户输入，有专门的测试守着它们指向真实模板，
    #:   不该因为运行环境里绑了个别的引擎就建不出租户来。
    _seeding: bool = False,
) -> CauseOfAction:
    if category not in CASE_CATEGORIES:
        raise DomainError(
            "案由大类必须是 {0} 之一".format("/".join(CASE_CATEGORIES)),
            code="case.bad_category",
        )
    if not name.strip():
        raise DomainError("案由名称不能为空", code="case.cause_name_required")
    dup = session.scalar(
        select(CauseOfAction).where(
            CauseOfAction.tenant_id == tenant_id, CauseOfAction.code == code.strip()
        )
    )
    if dup is not None:
        raise DomainError("案由编码 {0} 已存在".format(code), code="case.cause_code_taken")
    if parent_id is not None:
        get_cause(session, parent_id)

    cause = CauseOfAction(
        tenant_id=tenant_id, parent_id=parent_id, code=code.strip(), name=name.strip(),
        category=category,
        workflow_template=(workflow_template or "").strip() if _seeding
                          else _check_template(workflow_template),
        sort_order=sort_order,
    )
    session.add(cause)
    session.flush()
    return cause


def get_cause(session: Session, cause_id: int) -> CauseOfAction:
    cause = session.get(CauseOfAction, cause_id)
    if cause is None:
        raise NotFound("案由 {0} 不存在".format(cause_id))
    return cause


def list_causes(session: Session, tenant_id: int, active_only: bool = True) -> List[CauseOfAction]:
    stmt = select(CauseOfAction).where(CauseOfAction.tenant_id == tenant_id)
    if active_only:
        stmt = stmt.where(CauseOfAction.is_active.is_(True))
    return list(session.scalars(
        stmt.order_by(CauseOfAction.parent_id.nulls_first(),
                      CauseOfAction.sort_order, CauseOfAction.id)
    ))


#: 建所时的常用案由。律所可自行增删改 —— 这是数据不是代码。
def _check_template(key: str) -> str:
    """校验流程模板 key 真的存在。

    ★ 这道检查是审计文档时发现要加的：案由字典里存着 `construction_v1`、
      `labor_v1`，所务管理界面把它们当标签显示得像模像样，
      **而真实的模板 key 是 `construction.dispute`，`labor` 干脆没有模板**。
      也就是说这一栏从头到尾是**死数据**，没人用它选过流程，
      但它看起来是权威的 —— 下一个配案由的人会照着填，然后奇怪为什么没反应。

      **看起来在生效、其实没接线的字段，比空字段危险。**

      workflow 是可选依赖：没装就不校验（照样能建案由，只是这一栏没人管）。
    """
    key = (key or "").strip()
    if not key:
        return ""
    engine = None
    try:
        engine = container.get("workflow")
    except Exception as exc:                             # noqa: BLE001
        logger.warning("拿不到流程引擎，跳过模板校验：%s", exc)
        return key
    valid = engine.templates()
    if key not in valid:
        raise DomainError(
            "没有「{0}」这个流程模板。现有的是：{1}。\n"
            "留空表示这个案由暂时没有流程模板 —— 那样界面上不会显示，"
            "比填一个不存在的 key 强。".format(key, "、".join(valid) or "（一个都没有）"),
            code="case.bad_workflow_template")
    return key


#: (code, name, category, parent_code, workflow_template)
from modules.case.causes_civil import CIVIL_CAUSES        # noqa: E402
from modules.case.causes_civil2 import CIVIL_CAUSES_2     # noqa: E402

DEFAULT_CAUSES = (
    ("civil", "民事", "civil", None, ""),
    ("construction", "建设工程施工合同纠纷", "civil", "civil", "construction.dispute"),
    # ★ 劳动争议**还没有流程模板**，所以这里留空。
    #   原来写的是 "labor_v1" —— 一个不存在的 key，
    #   在所务管理界面上显示得像模像样，实际点不出任何东西。
    ("labor", "劳动争议", "civil", "civil", ""),
    ("sales", "买卖合同纠纷", "civil", "civil", ""),
    ("loan", "民间借贷纠纷", "civil", "civil", ""),
    ("equity", "股权转让纠纷", "civil", "civil", ""),
    ("admin", "行政", "admin", None, ""),
    ("admin_penalty", "行政处罚", "admin", "admin", ""),
    ("criminal", "刑事", "criminal", None, ""),
    ("nonlit", "非诉", "nonlit", None, ""),
    ("compliance", "合规与尽调", "nonlit", "nonlit", ""),
)


def ensure_default_causes(session: Session, tenant_id: int) -> List[CauseOfAction]:
    """建所时生成案由。已存在的跳过，可重复调用。

    ★ 民事那部分是**最高法《民事案件案由规定》（法〔2020〕347 号）的原文**，
      不是我们编的分类 —— 见 `causes_civil.py`。
      律所立案要和法院一致，自己造一套分类，出去的材料就对不上案号。
    """
    by_code: Dict[str, CauseOfAction] = {
        c.code: c for c in list_causes(session, tenant_id, active_only=False)
    }
    out: List[CauseOfAction] = []

    def _add(code, name, category, parent_code, template, order):
        if code in by_code:
            out.append(by_code[code])
            return
        parent = by_code.get(parent_code) if parent_code else None
        cause = create_cause(
            session, tenant_id, code, name, category,
            parent_id=parent.id if parent else None,
            workflow_template=template, sort_order=order, _seeding=True,
        )
        by_code[code] = cause
        out.append(cause)

    # 根节点与非民事部分
    for i, (code, name, category, parent_code, template) in enumerate(DEFAULT_CAUSES):
        _add(code, name, category, parent_code, template, i)

    # ★ 民事案由全表。挂在 "civil" 根下，**保持最高法的四级层次**。
    #   顺序照原文 —— 案由规定的排序本身有意义（部分/类的编号即顺序）。
    for i, (code, name, parent_code, template) in enumerate(
            CIVIL_CAUSES + CIVIL_CAUSES_2):
        _add(code, name, "civil", parent_code or "civil", template, 1000 + i)

    return out


# ================================================================ 案件


def _require_can_handle(session: Session, user_id: int) -> None:
    """主办律师必须是可独立承办的职级 —— 律师助理不行。

    规则来自 Step 3 的职级开关，**律所改一行数据就能放开**，不用改代码。
    """
    identity = registry.api("identity")
    position = identity.position_of(session, user_id)
    if position is None:
        raise DomainError("该用户尚未定级，不能担任主办律师", code="case.no_position")
    if not position.can_handle_case:
        raise DomainError(
            "{0} 当前未开启「可独立承办」，不能担任主办律师".format(position.name),
            code="case.cannot_handle_case",
        )

    # ★ 职级是**所里**给的，执业资格是**司法行政机关**给的 —— 两件事，都要查。
    #
    #   所里可以把谁提成合伙人，但提不出一张执业证。
    #   停止执业、已注销的人挂主办，一旦出去代理就是无证执业。
    #
    # ★ 只拦**硬失格**（停止执业 / 已注销）。
    #   "证过期了"不拦 —— 年检晚办几天很常见，把案子卡死没有道理；
    #   那种情况在人员页显眼地写出来，让人去核实。
    #   实习律师也不拦在这里：他的职级本来就没开「可独立承办」，
    #   上面那道已经拦住了，**同一件事拦两遍只会让报错互相打架**。
    try:
        lic = identity.license_of(session, user_id)
    except Exception:                                    # noqa: BLE001
        lic = None
    if lic and lic["status"] in ("suspended", "revoked"):
        raise DomainError(
            "{0} 的执业证状态是「{1}」，不能担任主办律师。\n"
            "先到「所务管理 → 人员」更新执业信息。".format(
                lic.get("full_name") or "该律师", lic["status_label"]),
            code="case.license_invalid",
        )


def create_case(
    session: Session,
    tenant_id: int,
    code: str,
    title: str,
    lead_id: int,
    cause_id: Optional[int] = None,
    client_id: Optional[int] = None,
    org_unit_id: Optional[int] = None,
    court: str = "",
    external_no: str = "",
    amount: Optional[Decimal] = None,
    opened_on: Optional[date] = None,
    summary: str = "",
) -> Case:
    """建案。**一律从 draft 起步** —— 利冲没过不能直接进入办理中。"""
    if not title.strip():
        raise DomainError("案件名称不能为空", code="case.title_required")
    dup = session.scalar(
        select(Case).where(Case.tenant_id == tenant_id, Case.code == code.strip())
    )
    if dup is not None:
        raise DomainError("案件编号 {0} 已存在".format(code), code="case.code_taken")
    if cause_id is not None:
        get_cause(session, cause_id)
    _require_can_handle(session, lead_id)

    case = Case(
        tenant_id=tenant_id, code=code.strip(), title=title.strip(),
        cause_id=cause_id, client_id=client_id, status="draft",
        lead_id=lead_id, org_unit_id=org_unit_id, court=court.strip(),
        external_no=external_no.strip(), amount=amount,
        opened_on=opened_on, summary=summary.strip(),
    )
    session.add(case)
    session.flush()
    add_member(session, tenant_id, case.id, lead_id, role="lead",
               joined_on=opened_on or date.today())
    # EVENT: case.created
    return case


def get_case(session: Session, case_id: int) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise NotFound("案件 {0} 不存在".format(case_id))
    return case


def wall_of(session: Session, principal):
    """当前主体在案件上的隔离墙视图。

    墙的数据在 identity，取法统一走这里 —— 免得每个查询各写一遍、漏掉一处。
    """
    identity = registry.api("identity")
    return identity.wall_view(session, principal.tenant_id, principal.user_id, "case")


def scoped_cases(session: Session, principal, stmt=None):
    """给案件查询套上「数据范围 + 隔离墙」。**所有案件列表都必须过这里。**

    ★ 顺序不能反：先按数据范围收窄，再用隔离墙剔除。
      隔离墙优先级更高 —— 管委会主任的 ALL 范围也会被它挡住。
    """
    from kernel import security

    base = select(Case) if stmt is None else stmt
    base = security.apply_scope(
        base, principal, "case.view",
        owner_col=Case.lead_id, org_col=Case.org_unit_id, tenant_col=Case.tenant_id,
    )
    return security.apply_wall(base, cast(Case.id, String), wall_of(session, principal))


def assert_visible(session: Session, principal, case_id: int) -> Case:
    """案件的单条访问校验：数据范围 + 隔离墙。

    **由 case 模块自己提供** —— 别的模块（conflict / 将来的 billing）要判断
    "这个人能不能看这个案子"，从这里拿，不去摸 case 的表。谁的数据谁定规则。
    """
    from kernel import security
    from kernel.errors import PermissionDenied

    case = get_case(session, case_id)

    # 隔离墙优先：措辞刻意不说"被隔离了"，否则等于承认该案件存在
    security.assert_not_walled(wall_of(session, principal), "案件", case_id)

    stmt = select(Case.id).where(Case.id == case_id)
    stmt = security.apply_scope(
        stmt, principal, "case.view",
        owner_col=Case.lead_id, org_col=Case.org_unit_id, tenant_col=Case.tenant_id,
    )
    if session.scalar(stmt) is None:
        raise PermissionDenied("无权访问该案件", code="case.out_of_scope")
    return case


#: 这些状态下案件**只读** —— 归档了、终止了，卷就定死了
FROZEN_STATUSES = ("archived", "terminated")


def assert_writable(session: Session, principal, case_id: int) -> Case:
    """看得见 **且** 写得动。所有往案件上写东西的地方都该走这一句。

    ★ 为什么这个检查放在 case 而不是各模块自己判：
      「什么状态算冻结」是案件的规则。八个模块各写一份 `if status ==`，
      迟早有人漏一个状态，或者加了新状态忘了同步 ——
      **判断标准只能有一份。**

    ★ 为什么不做成一个新模块去拦：那会成环。doc/time/billing 全都
      依赖 case，case 再去调它们就绕回来了。所以拦截留在 case
      （大家本来就依赖它），编目录那件事另开 archive 模块（只出不进）。

    ★ 归档不是终点。案子会再审、会申请执行 —— 解档见 archive 模块。
    """
    from kernel.errors import DomainError

    case = assert_visible(session, principal, case_id)
    if case.status in FROZEN_STATUSES:
        raise DomainError(
            "案件已「{0}」，卷已定死，不能再往里写东西。\n"
            "确需补充请先解档（需要管委会权限，并写明理由）。".format(
                STATUS_LABELS.get(case.status, case.status)),
            code="case.frozen")
    return case


def transition(
    session: Session,
    case_id: int,
    to_status: str,
    actor_id: Optional[int] = None,
    note: str = "",
) -> Case:
    """推进案件状态。**非法流转直接报错**，并留下流转记录。"""
    if to_status not in CASE_STATUSES:
        raise DomainError("未知的案件状态 {0}".format(to_status), code="case.bad_status")
    case = get_case(session, case_id)
    if case.status == to_status:
        return case
    if not can_transition(case.status, to_status):
        allowed = ALLOWED_TRANSITIONS.get(case.status, ())
        raise DomainError(
            "案件不能从「{0}」直接到「{1}」；可选：{2}".format(
                STATUS_LABELS.get(case.status, case.status),
                STATUS_LABELS.get(to_status, to_status),
                "、".join(STATUS_LABELS.get(s, s) for s in allowed) or "无",
            ),
            code="case.illegal_transition",
        )

    # ★ 执业红线：利冲没过不能进入「待立案」。
    #   用**可选依赖** —— conflict 模块没装就放行，装了就必须过。
    #   这样 case 不硬依赖 conflict，但只要装上，闸门自动生效。
    if to_status == "pending":
        conflict = registry.optional_api("conflict")
        if conflict is not None:
            conflict.assert_cleared(session, case_id)

    session.add(CaseStatusLog(
        case_id=case.id, from_status=case.status, to_status=to_status,
        actor_id=actor_id, note=note.strip(),
    ))
    case.status = to_status
    if to_status == "closed" and case.closed_on is None:
        case.closed_on = date.today()
    if to_status == "active" and case.opened_on is None:
        case.opened_on = date.today()
    session.flush()
    # EVENT: case.status_changed
    return case


def status_history(session: Session, case_id: int) -> List[CaseStatusLog]:
    return list(session.scalars(
        select(CaseStatusLog)
        .where(CaseStatusLog.case_id == case_id)
        .order_by(CaseStatusLog.id.desc())
    ))


def reassign_lead(session: Session, case_id: int, new_lead_id: int,
                  new_org_unit_id: Optional[int] = None) -> Case:
    """换主办（律师离所、团队调整）。改的是数据范围归属。"""
    _require_can_handle(session, new_lead_id)
    case = get_case(session, case_id)
    case.lead_id = new_lead_id
    if new_org_unit_id is not None:
        case.org_unit_id = new_org_unit_id
    existing = session.scalar(
        select(CaseMember).where(
            CaseMember.case_id == case_id, CaseMember.user_id == new_lead_id
        )
    )
    if existing is None:
        add_member(session, case.tenant_id, case_id, new_lead_id, role="lead")
    else:
        existing.role = "lead"
        existing.left_on = None
    session.flush()
    # EVENT: case.lead_reassigned
    return case


# ================================================================ 当事人


def add_party(
    session: Session,
    tenant_id: int,
    case_id: int,
    party_id: int,
    role: str,
    is_ours: bool = False,
    note: str = "",
) -> CaseParty:
    """登记当事人。party_id 指向 crm 主体库 —— 利冲要靠它。"""
    if role not in PARTY_ROLES:
        raise DomainError(
            "诉讼地位必须是 {0} 之一".format("/".join(PARTY_ROLES)),
            code="case.bad_party_role",
        )
    get_case(session, case_id)
    crm = registry.api("crm")
    crm.get_party(session, party_id)          # 主体必须先在库里，不允许现编名字

    dup = session.scalar(
        select(CaseParty).where(
            CaseParty.case_id == case_id, CaseParty.party_id == party_id,
            CaseParty.role == role,
        )
    )
    if dup is not None:
        return dup

    entry = CaseParty(
        tenant_id=tenant_id, case_id=case_id, party_id=party_id,
        role=role, is_ours=is_ours, note=note.strip(),
    )
    session.add(entry)
    session.flush()
    # EVENT: case.party_added
    return entry


def parties_of(session: Session, case_id: int) -> List[CaseParty]:
    return list(session.scalars(
        select(CaseParty).where(CaseParty.case_id == case_id)
        .order_by(CaseParty.is_ours.desc(), CaseParty.id)
    ))


def cases_of_party(
    session: Session,
    tenant_id: int,
    party_ids: Sequence[int],
    ours: Optional[bool] = None,
    open_only: bool = False,
) -> List[CaseParty]:
    """★ Step 7 利冲检查的核心查询。

    "这些主体在所里哪些案子里出现过？站在哪一边？"

    ours=False  只看对方 —— "我们有没有在别的案子里代理过他的对家"
    ours=True   只看我方 —— "他是不是我们的老客户"
    """
    if not party_ids:
        return []
    stmt = (
        select(CaseParty)
        .join(Case, Case.id == CaseParty.case_id)
        .where(CaseParty.tenant_id == tenant_id,
               CaseParty.party_id.in_(list(party_ids)))
    )
    if ours is not None:
        stmt = stmt.where(CaseParty.is_ours.is_(ours))
    if open_only:
        stmt = stmt.where(Case.status.in_(OPEN_STATUSES))
    return list(session.scalars(stmt.order_by(CaseParty.case_id)))


# ================================================================ 承办人


def add_member(
    session: Session,
    tenant_id: int,
    case_id: int,
    user_id: int,
    role: str = "member",
    joined_on: Optional[date] = None,
) -> CaseMember:
    if role not in CASE_MEMBER_ROLES:
        raise DomainError(
            "承办角色必须是 {0} 之一".format("/".join(CASE_MEMBER_ROLES)),
            code="case.bad_member_role",
        )
    get_case(session, case_id)
    exists = session.scalar(
        select(CaseMember).where(
            CaseMember.case_id == case_id, CaseMember.user_id == user_id
        )
    )
    if exists is not None:
        exists.role = role
        exists.left_on = None
        session.flush()
        return exists

    member = CaseMember(
        tenant_id=tenant_id, case_id=case_id, user_id=user_id, role=role,
        joined_on=joined_on or date.today(),
    )
    session.add(member)
    session.flush()
    return member


def remove_member(session: Session, case_id: int, user_id: int,
                  left_on: Optional[date] = None) -> CaseMember:
    """退出案件组 —— 关闭区间而不删记录，工时和文书还挂在他名下。"""
    member = session.scalar(
        select(CaseMember).where(
            CaseMember.case_id == case_id, CaseMember.user_id == user_id
        )
    )
    if member is None:
        raise NotFound("该用户不在本案承办人中")
    case = get_case(session, case_id)
    if member.role == "lead" and case.lead_id == user_id:
        raise DomainError("主办律师不能直接移除，请先改派主办", code="case.lead_locked")
    member.left_on = left_on or date.today()
    session.flush()
    return member


def members_of(session: Session, case_id: int, at: Optional[date] = None) -> List[CaseMember]:
    when = at or date.today()
    return list(session.scalars(
        select(CaseMember).where(
            CaseMember.case_id == case_id,
            CaseMember.joined_on <= when,
            or_(CaseMember.left_on.is_(None), CaseMember.left_on > when),
        ).order_by(CaseMember.role, CaseMember.id)
    ))


def case_ids_of_member(session: Session, user_id: int,
                       at: Optional[date] = None) -> List[int]:
    """我参与的案件（不只是我主办的）。工作台待办要用。"""
    when = at or date.today()
    return list(session.scalars(
        select(CaseMember.case_id).where(
            CaseMember.user_id == user_id,
            CaseMember.joined_on <= when,
            or_(CaseMember.left_on.is_(None), CaseMember.left_on > when),
        )
    ))


# ================================================================ 事项


def create_matter(
    session: Session,
    tenant_id: int,
    case_id: int,
    name: str,
    kind: str = "other",
    court: str = "",
    external_no: str = "",
    opened_on: Optional[date] = None,
    note: str = "",
) -> Matter:
    if kind not in MATTER_KINDS:
        raise DomainError(
            "事项类型必须是 {0} 之一".format("/".join(MATTER_KINDS)),
            code="case.bad_matter_kind",
        )
    case = get_case(session, case_id)
    if case.status in ("closed", "archived", "terminated"):
        raise DomainError(
            "案件已「{0}」，不能再新增事项".format(STATUS_LABELS.get(case.status)),
            code="case.closed_no_matter",
        )
    matter = Matter(
        tenant_id=tenant_id, case_id=case_id, name=name.strip(), kind=kind,
        court=court.strip(), external_no=external_no.strip(),
        opened_on=opened_on, note=note.strip(),
    )
    session.add(matter)
    session.flush()
    return matter


def matters_of(session: Session, case_id: int) -> List[Matter]:
    return list(session.scalars(
        select(Matter).where(Matter.case_id == case_id).order_by(Matter.id)
    ))


def set_matter_status(session: Session, matter_id: int, status: str) -> Matter:
    if status not in MATTER_STATUSES:
        raise DomainError("未知的事项状态", code="case.bad_matter_status")
    matter = session.get(Matter, matter_id)
    if matter is None:
        raise NotFound("事项 {0} 不存在".format(matter_id))
    matter.status = status
    if status == "done" and matter.closed_on is None:
        matter.closed_on = date.today()
    session.flush()
    return matter


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里 case 这部分：当事人、承办人、事项、状态流转。"""
    assert_visible(session, principal, case_id)
    out = []
    n = len(parties_of(session, case_id))
    if n:
        out.append({"name": "当事人", "count": n, "note": ""})
    n = len(members_of(session, case_id))
    if n:
        out.append({"name": "承办人", "count": n, "note": ""})
    n = len(matters_of(session, case_id))
    if n:
        out.append({"name": "事项", "count": n, "note": ""})
    n = len(status_history(session, case_id))
    if n:
        out.append({"name": "状态流转记录", "count": n, "note": ""})
    return out


def archive_blockers(session, principal, case_id):
    """归档前 case 这边还有什么没了结。"""
    case = assert_visible(session, principal, case_id)
    out = []
    # ★ 「案件还没结案」**不在这里**报 —— 它是归档的硬前提，
    #   由 archive.archive() 在最前面直接拦，force 也绕不过去。
    #
    #   放在 blockers 里是错的：blockers 是"可以强制跳过的事"，
    #   而状态机不让 draft 直接跳到 archived。第一版就这么写，结果
    #   force 一路放行、建完归档记录，最后卡在状态机上 ——
    #   **留下一条指向没归成档的案子的孤儿记录。**
    open_matters = [m for m in matters_of(session, case_id)
                    if m.status not in ("closed", "cancelled")]
    if open_matters:
        out.append({
            "what": "还有 {0} 个事项没结（{1}）".format(
                len(open_matters), "、".join(m.name for m in open_matters[:3])),
            "how": "把事项标为已结，或说明为什么留着",
        })
    return out


# ================================================================ 看板指标


def metrics(session, principal, spec=None):
    """案件这边能上看板的数：在办、本期新增、本期结案、按案由分布。"""
    spec = spec or {}
    since = spec.get("since")
    until = spec.get("until")

    # ★ scoped_cases 返回的是**查询语句**不是结果 —— 它是给别的查询
    #   继续往上叠条件用的。要行得自己执行一次。
    rows = list(session.scalars(scoped_cases(session, principal)).all())
    if spec.get("org_id"):
        rows = [c for c in rows if c.org_unit_id == spec["org_id"]]
    if spec.get("user_id"):
        rows = [c for c in rows if c.lead_id == spec["user_id"]]

    open_rows = [c for c in rows if c.status in OPEN_STATUSES]

    def _in(d):
        if d is None:
            return False
        if since and d < since:
            return False
        if until and d > until:
            return False
        return True

    new_rows = [c for c in rows if _in(c.opened_on)]
    closed_rows = [c for c in rows if _in(c.closed_on)]

    by_cause = {}
    for c in open_rows:
        name = "未填案由"
        if c.cause_id:
            try:
                name = get_cause(session, c.cause_id).name
            except Exception:                            # noqa: BLE001
                pass
        by_cause[name] = by_cause.get(name, 0) + 1

    return {
        "case.open": {"value": len(open_rows), "text": "%d 件" % len(open_rows)},
        "case.new": {"value": len(new_rows), "text": "%d 件" % len(new_rows)},
        "case.closed": {"value": len(closed_rows),
                        "text": "%d 件" % len(closed_rows)},
        "case.by_cause": {
            "value": len(by_cause),
            "text": "%d 类" % len(by_cause),
            # ★ 明细才是有用的东西 —— "5 类"这个数本身没意义
            "detail": [{"label": k, "value": v}
                       for k, v in sorted(by_cause.items(),
                                          key=lambda kv: -kv[1])],
        },
    }
