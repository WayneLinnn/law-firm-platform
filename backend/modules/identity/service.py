"""组织与身份的业务逻辑。

设计要点：
  · 组织树用 parent_id + 递归 CTE 查子树 —— 不做物化路径，避免移动节点时
    要同步更新所有后代（那是常见的数据不一致来源）。Postgres 的 WITH RECURSIVE
    在律所这个量级（几十到几百个节点）完全够用。
  · 所有归属查询都必须带时间点 `at`，默认今天。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import bcrypt
import jwt
from sqlalchemy import Select, and_, or_, select, text
from sqlalchemy.orm import Session

from kernel.config import settings
from kernel.errors import DomainError, NotFound, PermissionDenied, Unauthenticated
from modules.identity.models import (
    LEADER_ROLE,
    ORG_KINDS,
    POSITION_CATEGORIES,
    Appointment,
    Membership,
    OrgUnit,
    Position,
    Tenant,
    User,
)

# ---------------------------------------------------------------- 密码


def hash_password(raw: str) -> str:
    if len(raw) < 8:
        raise DomainError("密码至少 8 位", code="identity.weak_password")
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- JWT


def issue_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "tenant": user.tenant_id,
        "username": user.username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise Unauthenticated("登录已过期，请重新登录", code="identity.token_expired")
    except jwt.PyJWTError:
        raise Unauthenticated("无效的登录凭证", code="identity.token_invalid")


# ---------------------------------------------------------------- 租户


def create_tenant(session: Session, code: str, name: str) -> Tenant:
    if not code.strip() or not name.strip():
        raise DomainError("律所编码与名称不能为空", code="identity.tenant_required")
    tenant = Tenant(code=code.strip(), name=name.strip())
    session.add(tenant)
    session.flush()
    return tenant


def get_tenant(session: Session, tenant_id: int) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound("租户 {0} 不存在".format(tenant_id))
    return tenant


# ---------------------------------------------------------------- 组织树


def create_org_unit(
    session: Session,
    tenant_id: int,
    name: str,
    code: str,
    kind: str = "department",
    parent_id: Optional[int] = None,
    sort_order: int = 0,
) -> OrgUnit:
    if kind not in ORG_KINDS:
        raise DomainError(
            "组织类型必须是 {0} 之一".format("/".join(ORG_KINDS)),
            code="identity.bad_org_kind",
        )
    if not name.strip():
        raise DomainError("组织名称不能为空", code="identity.org_name_required")
    if parent_id is not None:
        parent = get_org_unit(session, parent_id)
        if parent.tenant_id != tenant_id:
            raise DomainError("不能挂到别的律所下面", code="identity.cross_tenant")

    unit = OrgUnit(
        tenant_id=tenant_id,
        parent_id=parent_id,
        kind=kind,
        name=name.strip(),
        code=code.strip(),
        sort_order=sort_order,
    )
    session.add(unit)
    session.flush()
    return unit


def get_org_unit(session: Session, org_unit_id: int) -> OrgUnit:
    unit = session.get(OrgUnit, org_unit_id)
    if unit is None:
        raise NotFound("组织单元 {0} 不存在".format(org_unit_id))
    return unit


def list_org_units(session: Session, tenant_id: int, active_only: bool = True) -> List[OrgUnit]:
    stmt = select(OrgUnit).where(OrgUnit.tenant_id == tenant_id)
    if active_only:
        stmt = stmt.where(OrgUnit.is_active.is_(True))
    stmt = stmt.order_by(OrgUnit.parent_id.nulls_first(), OrgUnit.sort_order, OrgUnit.id)
    return list(session.scalars(stmt))


def descendant_ids(session: Session, org_unit_id: int, include_self: bool = True) -> List[int]:
    """整棵子树的 id。Step 4 的 DEPT_TREE 数据范围直接用它。"""
    sql = text(
        """
        WITH RECURSIVE sub AS (
            SELECT id FROM identity.org_unit WHERE id = :root
            UNION ALL
            SELECT o.id FROM identity.org_unit o JOIN sub ON o.parent_id = sub.id
        )
        SELECT id FROM sub
        """
    )
    ids = [row[0] for row in session.execute(sql, {"root": org_unit_id})]
    if not include_self:
        ids = [i for i in ids if i != org_unit_id]
    return ids


def move_org_unit(session: Session, org_unit_id: int, new_parent_id: Optional[int]) -> OrgUnit:
    """部门拆分合并、团队改挂靠都走这里。"""
    unit = get_org_unit(session, org_unit_id)
    if new_parent_id is not None:
        if new_parent_id == org_unit_id:
            raise DomainError("不能把组织挂到自己下面", code="identity.org_cycle")
        if new_parent_id in descendant_ids(session, org_unit_id):
            raise DomainError("不能把组织挂到自己的后代下面", code="identity.org_cycle")
        parent = get_org_unit(session, new_parent_id)
        if parent.tenant_id != unit.tenant_id:
            raise DomainError("不能跨律所移动", code="identity.cross_tenant")
    unit.parent_id = new_parent_id
    session.flush()
    return unit


# ---------------------------------------------------------------- 用户


def create_user(
    session: Session,
    tenant_id: int,
    username: str,
    full_name: str,
    password: str,
    email: str = "",
    phone: str = "",
    is_superuser: bool = False,
) -> User:
    if not username.strip():
        raise DomainError("用户名不能为空", code="identity.username_required")
    exists = session.scalar(
        select(User).where(User.tenant_id == tenant_id, User.username == username.strip())
    )
    if exists is not None:
        raise DomainError("用户名 {0} 已存在".format(username), code="identity.username_taken")

    user = User(
        tenant_id=tenant_id,
        username=username.strip(),
        full_name=full_name.strip(),
        email=email.strip(),
        phone=phone.strip(),
        password_hash=hash_password(password),
        is_superuser=is_superuser,
    )
    session.add(user)
    session.flush()
    # EVENT: identity.user_created
    return user


def get_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFound("用户 {0} 不存在".format(user_id))
    return user


def find_user(session: Session, tenant_id: int, username: str) -> Optional[User]:
    return session.scalar(
        select(User).where(User.tenant_id == tenant_id, User.username == username)
    )


def authenticate(session: Session, tenant_code: str, username: str, password: str) -> User:
    tenant = session.scalar(select(Tenant).where(Tenant.code == tenant_code))
    if tenant is None or not tenant.is_active:
        # 不区分"律所不存在"和"密码错误"，避免探测
        raise PermissionDenied("用户名或密码错误", code="identity.bad_credentials")
    user = find_user(session, tenant.id, username)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise PermissionDenied("用户名或密码错误", code="identity.bad_credentials")
    return user


# ---------------------------------------------------------------- 归属


def _active_at(at: date) -> Sequence:
    """时间区间过滤，**半开区间 [valid_from, valid_to)**。

    ★ valid_to 是"失效日"而不是"最后一天"：3 月 1 日调岗，
      旧归属 valid_to=3/1、新归属 valid_from=3/1，3 月 1 日当天只属于新团队。

      用闭区间（>= at）的话，调岗当天这个人会**同时出现在新旧两个团队**，
      部门统计、权限范围、工时归集全都会重复计算。
    """
    return (
        Membership.valid_from <= at,
        or_(Membership.valid_to.is_(None), Membership.valid_to > at),
    )


def assign_membership(
    session: Session,
    user_id: int,
    org_unit_id: int,
    org_role: str = "",
    is_primary: bool = False,
    valid_from: Optional[date] = None,
) -> Membership:
    """把人加进一个组织，并指定在其中的角色。

    org_role 是"在这个组织里担任什么"（负责人 / 主任 / 组员），
    与职级（Position）无关 —— 职级跟人走，用 appoint()。

    律所规则：团队负责人必须是可牵头团队的职级（高级合伙人）。
    """
    user = get_user(session, user_id)
    unit = get_org_unit(session, org_unit_id)
    if user.tenant_id != unit.tenant_id:
        raise DomainError("用户与组织不属于同一律所", code="identity.cross_tenant")

    start = valid_from or date.today()
    dup = session.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.org_unit_id == org_unit_id,
            *_active_at(start),
        )
    )
    if dup is not None:
        raise DomainError("该用户在此组织已有生效归属", code="identity.membership_exists")

    role = org_role.strip()
    if unit.kind == "team" and role == LEADER_ROLE:
        _require_can_lead_team(session, user_id, start)

    if is_primary:
        _clear_primary(session, user_id, start)

    membership = Membership(
        tenant_id=user.tenant_id,
        user_id=user_id,
        org_unit_id=org_unit_id,
        org_role=role,
        is_primary=is_primary,
        valid_from=start,
    )
    session.add(membership)
    session.flush()
    return membership


def _clear_primary(session: Session, user_id: int, at: date) -> None:
    """一个人同一时刻只能有一个主归属。"""
    current = session.scalars(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.is_primary.is_(True),
            *_active_at(at),
        )
    )
    for m in current:
        m.is_primary = False


def end_membership(session: Session, membership_id: int, end_on: Optional[date] = None) -> Membership:
    """人员调岗 —— **不是删除记录，而是关闭时间区间**。

    历史统计要靠这段区间：3 月调走，1 月的案子仍然算在原部门。
    """
    membership = session.get(Membership, membership_id)
    if membership is None:
        raise NotFound("归属记录 {0} 不存在".format(membership_id))
    stop = end_on or date.today()
    if stop < membership.valid_from:
        raise DomainError("结束日期不能早于开始日期", code="identity.bad_date_range")
    membership.valid_to = stop
    session.flush()
    return membership


def memberships_of(
    session: Session, user_id: int, at: Optional[date] = None
) -> List[Membership]:
    """某人在某个时间点的全部归属（可能多条 —— 这就是矩阵结构）。"""
    when = at or date.today()
    stmt = (
        select(Membership)
        .where(Membership.user_id == user_id, *_active_at(when))
        .order_by(Membership.is_primary.desc(), Membership.id)
    )
    return list(session.scalars(stmt))


def members_of(
    session: Session,
    org_unit_id: int,
    at: Optional[date] = None,
    include_descendants: bool = False,
) -> List[Membership]:
    """某组织在某时间点的成员。include_descendants=True 时含整棵子树。"""
    when = at or date.today()
    org_ids = (
        descendant_ids(session, org_unit_id) if include_descendants else [org_unit_id]
    )
    stmt = (
        select(Membership)
        .where(Membership.org_unit_id.in_(org_ids), *_active_at(when))
        .order_by(Membership.org_unit_id, Membership.id)
    )
    return list(session.scalars(stmt))


def org_ids_of_user(
    session: Session, user_id: int, at: Optional[date] = None, include_descendants: bool = False
) -> List[int]:
    """某人当前能"代表"的组织 id 集合。Step 4 的 DataScope 会用它。"""
    ids: List[int] = []
    for m in memberships_of(session, user_id, at):
        if include_descendants:
            ids.extend(descendant_ids(session, m.org_unit_id))
        else:
            ids.append(m.org_unit_id)
    return sorted(set(ids))


# ================================================================ 职级 Position


#: 律所的默认职级表。建所时一键生成，之后律所可自行增删改（这是数据不是代码）。
#: (code, name, category, rank, is_partner, can_bill, can_lead_team, can_handle_case)
#: can_lead_team 是**可配置的默认值不是硬规则** —— 高伙、合伙人、独立律师都可以
#: 牵头团队；助理默认不行，但律所若要放开，改这一行数据即可，不用动代码。
DEFAULT_POSITIONS = (
    ("senior_partner", "高级合伙人", "lawyer", 100, True, True, True, True),
    ("partner", "合伙人", "lawyer", 80, True, True, True, True),
    ("associate", "独立律师", "lawyer", 60, False, True, True, True),
    ("paralegal", "律师助理", "lawyer", 40, False, True, False, False),
    ("finance", "财务", "finance", 20, False, False, False, False),
    ("admin", "行政", "admin", 20, False, False, False, False),
)


def create_position(
    session: Session,
    tenant_id: int,
    code: str,
    name: str,
    category: str = "lawyer",
    rank: int = 0,
    is_partner: bool = False,
    can_bill: bool = False,
    can_lead_team: bool = False,
    can_handle_case: bool = False,
) -> Position:
    if category not in POSITION_CATEGORIES:
        raise DomainError(
            "人员大类必须是 {0} 之一".format("/".join(POSITION_CATEGORIES)),
            code="identity.bad_category",
        )
    if not name.strip():
        raise DomainError("职级名称不能为空", code="identity.position_name_required")
    exists = session.scalar(
        select(Position).where(Position.tenant_id == tenant_id, Position.code == code)
    )
    if exists is not None:
        raise DomainError("职级 {0} 已存在".format(code), code="identity.position_exists")

    position = Position(
        tenant_id=tenant_id, code=code.strip(), name=name.strip(), category=category,
        rank=rank, is_partner=is_partner, can_bill=can_bill,
        can_lead_team=can_lead_team, can_handle_case=can_handle_case,
    )
    session.add(position)
    session.flush()
    return position


def ensure_default_positions(session: Session, tenant_id: int) -> List[Position]:
    """建所时生成默认职级。已存在的跳过，可重复调用。"""
    out: List[Position] = []
    for code, name, cat, rank, partner, bill, lead, handle in DEFAULT_POSITIONS:
        found = session.scalar(
            select(Position).where(Position.tenant_id == tenant_id, Position.code == code)
        )
        out.append(found or create_position(
            session, tenant_id, code, name, cat, rank, partner, bill, lead, handle
        ))
    return out


def list_positions(session: Session, tenant_id: int, active_only: bool = True) -> List[Position]:
    stmt = select(Position).where(Position.tenant_id == tenant_id)
    if active_only:
        stmt = stmt.where(Position.is_active.is_(True))
    return list(session.scalars(stmt.order_by(Position.rank.desc(), Position.id)))


def get_position(session: Session, tenant_id: int, code: str) -> Position:
    position = session.scalar(
        select(Position).where(Position.tenant_id == tenant_id, Position.code == code)
    )
    if position is None:
        raise NotFound("职级 {0} 不存在".format(code))
    return position


# ================================================================ 任职 Appointment


def _appointment_active_at(at: date):
    """同样是半开区间 —— 晋升当天只算新职级，不会新旧并存。"""
    return (
        Appointment.valid_from <= at,
        or_(Appointment.valid_to.is_(None), Appointment.valid_to > at),
    )


def appoint(
    session: Session,
    user_id: int,
    position_code: str,
    valid_from: Optional[date] = None,
    note: str = "",
) -> Appointment:
    """入职定级 / 晋升。

    **不改字段，而是关闭旧区间 + 开新区间** —— 6 月晋升合伙人，
    5 月的案件分配仍按原职级算，否则账全错。

    由管委会决定（Step 4 会加权限点 identity.promote）。
    """
    user = get_user(session, user_id)
    position = get_position(session, user.tenant_id, position_code)
    start = valid_from or date.today()

    current = session.scalar(
        select(Appointment).where(Appointment.user_id == user_id, *_appointment_active_at(start))
    )
    if current is not None:
        if current.position_id == position.id:
            raise DomainError("该用户当前已是此职级", code="identity.same_position")
        if start <= current.valid_from:
            raise DomainError("生效日期必须晚于当前任职的开始日期",
                              code="identity.bad_date_range")
        current.valid_to = start

    appointment = Appointment(
        tenant_id=user.tenant_id, user_id=user_id, position_id=position.id,
        valid_from=start, note=note.strip(),
    )
    session.add(appointment)
    session.flush()
    # EVENT: identity.user_appointed
    return appointment


def position_of(session: Session, user_id: int, at: Optional[date] = None) -> Optional[Position]:
    """某人在某时间点的职级。没有任职记录返回 None。"""
    when = at or date.today()
    appointment = session.scalar(
        select(Appointment).where(Appointment.user_id == user_id, *_appointment_active_at(when))
    )
    return session.get(Position, appointment.position_id) if appointment else None


def appointment_history(session: Session, user_id: int) -> List[Appointment]:
    """完整晋升履历，最新在前。"""
    return list(session.scalars(
        select(Appointment)
        .where(Appointment.user_id == user_id)
        .order_by(Appointment.valid_from.desc())
    ))


def _require_can_lead_team(session: Session, user_id: int, at: date) -> None:
    """能否牵头团队由**职级表上的开关**决定，不是写死的规则。

    默认高伙 / 合伙人 / 独立律师都可以，助理不行 —— 律所要放开就改数据。
    """
    position = position_of(session, user_id, at)
    if position is None:
        raise DomainError("该用户尚未定级，不能担任团队负责人",
                          code="identity.no_position")
    if not position.can_lead_team:
        raise DomainError(
            "{0} 当前未开启「可牵头团队」，如需放开请在职级设置中调整".format(position.name),
            code="identity.cannot_lead_team",
        )


# ================================================================ 团队


def leader_of(session: Session, team_id: int, at: Optional[date] = None) -> Optional[Membership]:
    """团队负责人（牵头的高级合伙人）。"""
    when = at or date.today()
    return session.scalar(
        select(Membership).where(
            Membership.org_unit_id == team_id,
            Membership.org_role == LEADER_ROLE,
            *_active_at(when),
        )
    )


def teams_of(session: Session, user_id: int, at: Optional[date] = None) -> List[OrgUnit]:
    """某人所在的全部团队（律师基本都在团队里运作）。"""
    when = at or date.today()
    org_ids = [m.org_unit_id for m in memberships_of(session, user_id, when)]
    if not org_ids:
        return []
    return list(session.scalars(
        select(OrgUnit).where(OrgUnit.id.in_(org_ids), OrgUnit.kind == "team")
    ))


def committee_members(
    session: Session, tenant_id: int, at: Optional[date] = None
) -> List[Membership]:
    """管委会成员 —— 权力机构，决定人员出入与晋升。"""
    when = at or date.today()
    committees = session.scalars(
        select(OrgUnit.id).where(OrgUnit.tenant_id == tenant_id, OrgUnit.kind == "committee")
    ).all()
    if not committees:
        return []
    return list(session.scalars(
        select(Membership).where(Membership.org_unit_id.in_(committees), *_active_at(when))
    ))


def is_committee_member(session: Session, user_id: int, at: Optional[date] = None) -> bool:
    user = get_user(session, user_id)
    return any(m.user_id == user_id for m in committee_members(session, user.tenant_id, at))


# ---------------------------------------------------------------- 执业资格

LICENSE_STATUSES = ("practising", "intern", "suspended", "revoked")

LICENSE_LABEL = {
    "practising": "执业中",
    "intern": "实习",
    "suspended": "停止执业",
    "revoked": "已注销",
    "": "未登记",
}

#: 能独立办案的执业状态。★ 实习律师不在里面 —— 实习期不能独立承办、
#: 不能单独出庭，这是《律师执业管理办法》定的，不是所里的规矩。
LICENSE_CAN_PRACTISE = ("practising",)


def set_license(
    session: Session,
    user_id: int,
    license_no: str = "",
    status: str = "",
    licensed_on: Optional[date] = None,
    valid_to: Optional[date] = None,
    firm: str = "",
) -> User:
    """登记 / 更新执业资格。"""
    if status and status not in LICENSE_STATUSES:
        raise DomainError(
            "执业状态必须是 {0} 之一".format("/".join(LICENSE_STATUSES)),
            code="identity.bad_license_status",
        )
    user = get_user(session, user_id)

    # ★ 号码要查重：一个执业证号只能挂一个人。
    #   两个人挂同一个号，多半是录错了 —— 而错的那个会一路错到委托书上。
    no = (license_no or "").strip()
    if no:
        dup = session.scalar(select(User).where(
            User.tenant_id == user.tenant_id,
            User.license_no == no,
            User.id != user_id,
        ))
        if dup is not None:
            raise DomainError(
                "执业证号 {0} 已登记在 {1} 名下".format(no, dup.full_name),
                code="identity.license_taken",
            )

    user.license_no = no
    user.license_status = (status or "").strip()
    user.licensed_on = licensed_on
    user.license_valid_to = valid_to
    user.license_firm = (firm or "").strip()
    session.flush()
    return user


def license_of(session: Session, user_id: int,
               today: Optional[date] = None) -> Dict[str, Any]:
    """一个人的执业资格现状 —— 界面上那一块显示的东西。

    ★ **过期不等于失效。** 年检晚办几天很常见，证还是那本证。
      所以这里只把"到期了"报出来（`expired`），
      **不擅自把人当成没资格** —— 那会在没人预料的时候把案子卡死。

    ★ 但也**不能不说**：一个"执业中"的绿标底下压着一张三年前就过期的证，
      比什么都不显示更糟 —— 它让人以为查过了。
    """
    today = today or date.today()
    u = get_user(session, user_id)
    st = u.license_status or ""
    expired = bool(u.license_valid_to and u.license_valid_to < today)

    if not st:
        note = "未登记执业信息"
    elif st == "intern":
        note = "实习期 —— 不能独立承办、不能单独出庭"
    elif st in ("suspended", "revoked"):
        note = "已{0}，不能承办新案件".format(LICENSE_LABEL[st])
    elif expired:
        note = "证载有效期已过（{0}）—— 多半是年检没办，去核实".format(
            u.license_valid_to.isoformat())
    else:
        note = ""

    return {
        "user_id": u.id,
        "full_name": u.full_name,
        "license_no": u.license_no,
        "status": st,
        "status_label": LICENSE_LABEL.get(st, st),
        "licensed_on": u.licensed_on.isoformat() if u.licensed_on else "",
        "valid_to": u.license_valid_to.isoformat() if u.license_valid_to else "",
        "firm": u.license_firm,
        "expired": expired,
        #: 能不能独立承办 —— 主办律师那道检查用它
        "can_practise": st in LICENSE_CAN_PRACTISE,
        "note": note,
    }
