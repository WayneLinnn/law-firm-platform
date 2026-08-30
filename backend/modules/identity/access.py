"""角色、授权、主体装配、隔离墙。

与 service.py 的分工：
    service.py  组织、用户、职级、归属 —— "谁是谁、在哪儿"
    access.py   角色、权限、数据范围、隔离墙 —— "能看什么、能做什么"

**业务代码永远不判断角色名。** `role == "合伙人"` 一旦出现，律所改一次
角色配置就要改代码。只判断权限点（kernel.security.require）。
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence, Set

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import DataScope, Principal, WallView
from modules.identity.models import (
    WALL_MODES,
    EthicalWall,
    Role,
    RoleAssignment,
    RolePermission,
)
from modules.identity.service import (
    descendant_ids,
    get_user,
    org_ids_of_user,
)

# ---------------------------------------------------------------- 默认角色


#: 建所默认角色。权限点由各模块在 manifest 里注册，加模块自动多出可选项。
#: (code, name, data_scope, description, permissions)
DEFAULT_ROLES = (
    ("committee", "管委会", DataScope.ALL,
     "权力机构：决定人员出入与晋升，可查全所",
     ("identity.view", "identity.manage_org", "identity.manage_user",
      "identity.promote", "identity.manage_role", "identity.manage_wall",
      "audit.view",
      "crm.view", "crm.manage", "crm.manage_party", "crm.transfer",
      "case.view", "case.manage", "case.close", "case.manage_cause",
      "conflict.run", "conflict.view", "conflict.decide",
      "retrieval.search", "retrieval.ask", "retrieval.view", "retrieval.cost",
      "workflow.start", "workflow.view", "workflow.resume", "workflow.cancel",
      "doc.view", "doc.upload", "doc.manage", "doc.delete",
      "draft.view", "draft.create", "draft.finalize", "citation.view",
      "task.view", "task.manage", "task.deadline", "task.manage_rule",
      "consult.log", "consult.view", "consult.view_all", "consult.manage_rate",
      "billing.view", "billing.manage", "billing.issue",
      "billing.payment", "billing.manage_rate",
      "approval.view", "approval.request", "approval.approve_team",
      "approval.approve_firm", "approval.manage_flow",
      "archive.view", "archive.do", "archive.unarchive", "archive.force",
      "analytics.view", "analytics.view_firm")),
    ("team_leader", "团队负责人", DataScope.DEPT_TREE,
     "可查本团队及其下级的数据",
     ("identity.view", "crm.view", "crm.manage", "crm.manage_party", "crm.transfer",
      "case.view", "case.manage", "case.close",
      "conflict.run", "conflict.view",
      "retrieval.search", "retrieval.ask", "retrieval.view",
      "workflow.start", "workflow.view", "workflow.resume", "workflow.cancel",
      "doc.view", "doc.upload", "doc.manage", "doc.delete",
      "draft.view", "draft.create", "draft.finalize", "citation.view",
      "task.view", "task.manage", "task.deadline", "task.manage_rule",
      # ★ 团队负责人看得到本团队的咨询记录（谁接了咨询、收了多少）。
      #   **没有审批环节** —— 一次咨询是已经发生的收费事实，
      #   不像工时那样需要"这 40 小时该不该报给客户"的判断，
      #   加一道审批只会让人拖着不记。
      "consult.log", "consult.view", "consult.view_all",
      # ★ 能谈收费、能编草稿账单，但**不给 issue 和 payment** ——
      #   开出账单和确认收款是财务动作。业务口开单、业务口销账，
      #   就没有第二双眼睛了
      "billing.view", "billing.manage",
      # ★ 团队负责人只批**团队这一道**，不给 approve_firm ——
      #   五千以上要过管委会，正是为了让第二个人看一眼。
      #   把两道权限给同一个人，两道就变成了一道
      "approval.view", "approval.request", "approval.approve_team",
      # ★ 团队负责人能归档，但**不给 force 和 unarchive** ——
      #   带着未收的账强制归档、或者把封好的卷解开，都该是管委会的判断
      "archive.view", "archive.do",
      # ★ 团队负责人看自己和团队，**不给 view_firm** ——
      #   全所创收是管委会的事，不是每个团队都该看到别的团队挣多少
      "analytics.view")),
    ("lawyer", "律师", DataScope.SELF,
     "只看自己经办的",
     # ★ 建主体/建客户**给律师** —— 合伙人、独立律师本来就在自己拉客户，
     #   把这事收到管委会手里，结果只会是律师微信里报一句、
     #   助理代录一遍，中间那一遍就是**利冲漏检的入口**。
     #   律师助理不能建：他没有"可独立承办"这个职级开关，
     #   拦在 crm.service._require_can_handle 那里 —— **角色管按钮，职级管资格**。
     ("identity.view", "crm.view", "crm.manage", "crm.manage_party",
      "case.view", "case.manage",
      "conflict.run", "conflict.view",
      "retrieval.search", "retrieval.ask", "retrieval.view",
      # ★ 律师能起流程、能确认，但**不给 workflow.cancel** ——
      #   中止会让一条已经走了一半的流程停在半路，属于团队负责人的判断。
      "workflow.start", "workflow.view", "workflow.resume",
      # ★ 律师能传能改，**不给 doc.delete** —— 案卷材料删不得，
      #   要删由团队负责人判断（而且只是软删，本体还在）
      "doc.view", "doc.upload", "doc.manage",
      # ★ 律师能起草，**定稿也给他** —— 文书是他签字的东西，
      #   定稿这个动作本来就该由承办律师做，不是找人代批
      "draft.view", "draft.create", "draft.finalize", "citation.view",
      # ★ 律师能确认自己案子的起算日，**不给 task.manage_rule** ——
      #   时限规则是全所统一口径，一个人改了所有人跟着变
      "task.view", "task.manage", "task.deadline",
      # ★ 律师只能记和看**自己**的咨询。
      #   不给 consult.view_all：咨询摘要里写着当事人来说了什么，
      #   那是隔离墙挡的东西，不能从咨询表这个后门漏出去。
      "consult.log", "consult.view",
      # ★ 律师看得到自己案子的收费情况（客户会当面问"我还欠多少"），
      #   但**签约定、开账单、收款都不给** —— 那三件事各有各的把关人
      "billing.view",
      # ★ 律师能提单、能看，**批不了任何一道** —— 包括自己的
      #   （service 里另有硬拦：不能批自己提的单）
      "approval.view", "approval.request",
      "archive.view",
      # 律师只看得到自己主办的那部分
      "analytics.view")),
    ("finance", "财务", DataScope.ALL,
     "财务口径要看全所金额",
     # ★ 财务要出账单，所以看得到全所咨询；但**不给 consult.log** ——
     #   记咨询是业务动作，财务代记就说不清这一小时是谁聊的了
     ("identity.view", "crm.view", "case.view", "conflict.view",
      "consult.view", "consult.view_all",
      # ★ 财务开单、收款、维护费率表；**不给 billing.manage** ——
      #   收费怎么谈是业务的事，财务改了约定就说不清是谁答应客户的
      "billing.view", "billing.issue", "billing.payment", "billing.manage_rate",
      # ★ 财务只**付款**，不批单。批的是"这钱该不该花"（业务判断），
      #   付的是"钱确实出去了"（执行）。同一个人既批又付，
      #   等于自己批自己付 —— 这是所有财务内控的第一条红线
      "approval.view", "approval.pay", "archive.view",
      # ★ 财务看全所金额 —— 那本来就是他的口径
      "analytics.view", "analytics.view_firm")),
    ("staff", "行政", DataScope.DEPT,
     "本部门范围",
     # ★ 用印执行归行政。给 approval.seal 但**不给 approve_team** ——
     #   盖章的人不该同时是批准盖章的人
     ("identity.view", "approval.view", "approval.request", "approval.seal",
      "archive.view")),
)


# ---------------------------------------------------------------- 角色


def create_role(
    session: Session,
    tenant_id: int,
    code: str,
    name: str,
    data_scope: str = DataScope.SELF,
    description: str = "",
    permissions: Sequence[str] = (),
) -> Role:
    if data_scope not in DataScope.ALL_VALUES:
        raise DomainError(
            "数据范围必须是 {0} 之一".format("/".join(DataScope.ORDER)),
            code="identity.bad_scope",
        )
    exists = session.scalar(
        select(Role).where(Role.tenant_id == tenant_id, Role.code == code)
    )
    if exists is not None:
        raise DomainError("角色 {0} 已存在".format(code), code="identity.role_exists")

    role = Role(
        tenant_id=tenant_id, code=code.strip(), name=name.strip(),
        data_scope=data_scope, description=description.strip(),
    )
    session.add(role)
    session.flush()
    set_role_permissions(session, role.id, permissions)
    return role


def set_role_permissions(
    session: Session, role_id: int, permissions: Sequence[str]
) -> None:
    """整体覆盖角色的权限点。

    只接受已注册的权限点 —— 拼错的权限点会静默失效，是最难查的一类 bug。
    """
    known = set(registry.permissions())
    unknown = [p for p in permissions if p not in known]
    if unknown:
        raise DomainError(
            "未注册的权限点：{0}（各模块在 manifest.py 里声明）".format("、".join(unknown)),
            code="identity.unknown_permission",
        )
    session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    for perm in dict.fromkeys(permissions):
        session.add(RolePermission(role_id=role_id, permission=perm))
    session.flush()


def ensure_default_roles(session: Session, tenant_id: int) -> List[Role]:
    """建所时生成默认角色。已存在的跳过，可重复调用。

    只授予**当前已注册**的权限点 —— 后续模块（case/billing…）上线后，
    它们的权限点会自动出现在可选列表里，由律所再补进角色。
    """
    known = set(registry.permissions())
    out: List[Role] = []
    for code, name, scope, desc, perms in DEFAULT_ROLES:
        found = session.scalar(
            select(Role).where(Role.tenant_id == tenant_id, Role.code == code)
        )
        if found is not None:
            out.append(found)
            continue
        out.append(create_role(
            session, tenant_id, code, name, scope, desc,
            tuple(p for p in perms if p in known),
        ))
    return out


def list_roles(session: Session, tenant_id: int) -> List[Role]:
    return list(session.scalars(
        select(Role).where(Role.tenant_id == tenant_id).order_by(Role.id)
    ))


def get_role(session: Session, tenant_id: int, code: str) -> Role:
    role = session.scalar(
        select(Role).where(Role.tenant_id == tenant_id, Role.code == code)
    )
    if role is None:
        raise NotFound("角色 {0} 不存在".format(code))
    return role


def role_permissions(session: Session, role_id: int) -> List[str]:
    return list(session.scalars(
        select(RolePermission.permission).where(RolePermission.role_id == role_id)
    ))


# ---------------------------------------------------------------- 授权


def assign_role(
    session: Session,
    user_id: int,
    role_code: str,
    org_unit_id: Optional[int] = None,
    valid_from: Optional[date] = None,
) -> RoleAssignment:
    """给人授予角色。

    org_unit_id 为空 = 全所生效；填了 = 只在该组织（含下级）内生效，
    对应 CUSTOM 数据范围。
    """
    user = get_user(session, user_id)
    role = get_role(session, user.tenant_id, role_code)
    start = valid_from or date.today()

    stmt = select(RoleAssignment).where(
        RoleAssignment.user_id == user_id,
        RoleAssignment.role_id == role.id,
        RoleAssignment.valid_from <= start,
        or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > start),
    )
    stmt = (
        stmt.where(RoleAssignment.org_unit_id.is_(None))
        if org_unit_id is None
        else stmt.where(RoleAssignment.org_unit_id == org_unit_id)
    )
    if session.scalar(stmt) is not None:
        raise DomainError("该用户已有此角色授权", code="identity.role_assigned")

    assignment = RoleAssignment(
        tenant_id=user.tenant_id, user_id=user_id, role_id=role.id,
        org_unit_id=org_unit_id, valid_from=start,
    )
    session.add(assignment)
    session.flush()
    # EVENT: identity.role_assigned
    return assignment


def revoke_role(
    session: Session, assignment_id: int, end_on: Optional[date] = None
) -> RoleAssignment:
    """收回授权 —— 关闭时间区间而不删记录。

    审计要能回答"去年 3 月谁有查全所的权限"，删了就查不出来了。
    """
    assignment = session.get(RoleAssignment, assignment_id)
    if assignment is None:
        raise NotFound("角色授权 {0} 不存在".format(assignment_id))
    assignment.valid_to = end_on or date.today()
    session.flush()
    return assignment


def roles_of(
    session: Session, user_id: int, at: Optional[date] = None
) -> List[RoleAssignment]:
    """半开区间 [valid_from, valid_to) —— 收回授权当天即刻失效。"""
    when = at or date.today()
    return list(session.scalars(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.valid_from <= when,
            or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > when),
        )
    ))


# ---------------------------------------------------------------- 主体装配


def build_principal(
    session: Session, user_id: int, at: Optional[date] = None
) -> Principal:
    """把一个用户在某时间点的全部权限装配成内核认识的 Principal。

    这是权限体系的**唯一出口** —— 内核和业务模块都不知道角色是怎么配的，
    将来换权限模型只改这一个函数。
    """
    user = get_user(session, user_id)
    when = at or date.today()

    perms: Set[str] = set()
    scopes: Dict[str, List[str]] = {}
    custom_orgs: Set[int] = set()

    for assignment in roles_of(session, user_id, when):
        role = session.get(Role, assignment.role_id)
        if role is None or not role.is_active:
            continue
        scope = role.data_scope
        if assignment.org_unit_id is not None:
            # 限定了组织的授权 —— 收窄为 CUSTOM 范围
            scope = DataScope.CUSTOM
            custom_orgs.update(descendant_ids(session, assignment.org_unit_id))
        for perm in role_permissions(session, role.id):
            perms.add(perm)
            scopes.setdefault(perm, []).append(scope)

    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        is_superuser=user.is_superuser,
        permissions=frozenset(perms),
        # 多个角色授予同一权限点时取最宽的范围
        scopes={p: DataScope.widest(v) for p, v in scopes.items()},
        org_ids=tuple(org_ids_of_user(session, user_id, when)),
        org_tree_ids=tuple(org_ids_of_user(session, user_id, when,
                                           include_descendants=True)),
        custom_org_ids=tuple(sorted(custom_orgs)),
    )


# ---------------------------------------------------------------- 隔离墙


def set_wall(
    session: Session,
    tenant_id: int,
    resource_type: str,
    resource_id: str,
    user_id: int,
    mode: str = "deny",
    reason: str = "",
    created_by: Optional[int] = None,
) -> EthicalWall:
    """设隔离墙。

    deny  —— 禁止此人访问该资源（利冲隔离）
    allow —— 把此人加入该资源的白名单（涉密案件；一旦有 allow 记录，
             名单外的人一律不可见，主任也不例外）
    """
    if mode not in WALL_MODES:
        raise DomainError("隔离墙模式必须是 deny 或 allow", code="identity.bad_wall_mode")

    # ★ 幂等。叫 set_ 就不能"第二次调用炸掉" ——
    #   利冲放行时会带着同一批人重复设墙（改理由、重新处置），
    #   uq_wall_entry 会直接 IntegrityError，整个放行事务回滚，
    #   管委会点"放行"看到的是 500。已经挡住的人再挡一次，本就该是无事发生。
    entry = session.scalar(
        select(EthicalWall).where(
            EthicalWall.tenant_id == tenant_id,
            EthicalWall.resource_type == resource_type,
            EthicalWall.resource_id == str(resource_id),
            EthicalWall.user_id == user_id,
            EthicalWall.mode == mode,
        )
    )
    if entry is not None:
        # 理由可能变了（换了一次会议决议），覆盖；墙本身不动
        if reason.strip():
            entry.reason = reason.strip()
        session.flush()
        return entry

    entry = EthicalWall(
        tenant_id=tenant_id, resource_type=resource_type,
        resource_id=str(resource_id), user_id=user_id, mode=mode,
        reason=reason.strip(), created_by=created_by,
    )
    session.add(entry)
    session.flush()
    return entry


def clear_wall(
    session: Session, tenant_id: int, resource_type: str, resource_id: str
) -> int:
    """撤销某资源上的全部隔离墙（利冲解除时用）。返回删除条数。"""
    result = session.execute(
        delete(EthicalWall).where(
            EthicalWall.tenant_id == tenant_id,
            EthicalWall.resource_type == resource_type,
            EthicalWall.resource_id == str(resource_id),
        )
    )
    session.flush()
    return result.rowcount or 0


def walls_on(
    session: Session, tenant_id: int, resource_type: str, resource_id: str
) -> List[EthicalWall]:
    """列出某资源上的全部隔离墙。

    ★ 墙必须**看得见**。看不见的墙 = 有人莫名其妙访问不了案件，
      没人知道是谁在什么时候挡的 —— 排查不了，也撤不掉。
    """
    return list(session.scalars(
        select(EthicalWall).where(
            EthicalWall.tenant_id == tenant_id,
            EthicalWall.resource_type == resource_type,
            EthicalWall.resource_id == str(resource_id),
        ).order_by(EthicalWall.id)
    ).all())


def remove_wall(session: Session, tenant_id: int, wall_id: int) -> EthicalWall:
    """撤销一堵墙。返回被删掉的那条，供审计留痕。

    ★ 这个操作**不检查调用者能否看到该资源** —— 权限点是
      identity.manage_wall，不是 case.view。理由：把自己挡在外面
      是很容易的误操作，如果撤墙还要求先能看见案件，那就永远撤不掉了。
    """
    entry = session.get(EthicalWall, wall_id)
    if entry is None or entry.tenant_id != tenant_id:
        raise NotFound("隔离墙不存在", code="identity.wall_not_found")
    session.delete(entry)
    session.flush()
    return entry


def wall_view(
    session: Session, tenant_id: int, user_id: int, resource_type: str
) -> WallView:
    """装配某人在某类资源上的隔离墙视图，交给内核做过滤。"""
    rows = list(session.scalars(
        select(EthicalWall).where(
            EthicalWall.tenant_id == tenant_id,
            EthicalWall.resource_type == resource_type,
        )
    ))
    denied: Set[str] = set()
    restricted: Set[str] = set()
    allowed: Set[str] = set()
    for row in rows:
        if row.mode == "deny":
            if row.user_id == user_id:
                denied.add(row.resource_id)
        else:
            restricted.add(row.resource_id)
            if row.user_id == user_id:
                allowed.add(row.resource_id)
    return WallView(
        denied_ids=frozenset(denied),
        restricted_ids=frozenset(restricted),
        allowed_ids=frozenset(allowed),
    )
