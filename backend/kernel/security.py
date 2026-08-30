"""权限机制。**只有机制，没有表** —— 角色、授权、隔离墙的数据在 identity 模块。

两件事必须分清（很多系统栽在混为一谈）：

    功能权限  能不能点这个按钮        —— 权限点 permission
    数据权限  能看到哪些行            —— 数据范围 DataScope

业务代码只做两件事：

    security.require(principal, "case.view")                     # 功能权限
    stmt = security.apply_scope(stmt, principal, "case.view",     # 数据权限
                                owner_col=Case.owner_id,
                                org_col=Case.org_unit_id)

**永远不要在业务代码里判断角色名。** `role == "合伙人"` 这种写法一旦出现，
律所改一次角色配置就要改代码。只判断权限点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, FrozenSet, Iterable, Mapping, Optional, Sequence, Set

from sqlalchemy import Select, and_, or_

from kernel.errors import PermissionDenied


# ---------------------------------------------------------------- 数据范围


class DataScope:
    """能看到哪些行。数值越大范围越宽。"""

    SELF = "self"            # 仅本人
    DEPT = "dept"            # 本部门/团队（直属）
    DEPT_TREE = "dept_tree"  # 本部门及其下级
    CUSTOM = "custom"        # 授权时指定的组织
    ALL = "all"              # 全所

    ORDER = (SELF, DEPT, CUSTOM, DEPT_TREE, ALL)
    ALL_VALUES = frozenset(ORDER)

    @classmethod
    def widest(cls, scopes: Iterable[str]) -> str:
        """一个人可能有多个角色都授予同一权限点，取最宽的那个范围。"""
        best = cls.SELF
        for s in scopes:
            if s in cls.ALL_VALUES and cls.ORDER.index(s) > cls.ORDER.index(best):
                best = s
        return best


# ---------------------------------------------------------------- 隔离墙


@dataclass(frozen=True)
class WallView:
    """某个主体在某类资源上的隔离墙视图。**优先级高于一切数据范围。**

    两种模式并存：
      deny  —— 明确禁止某人看某个案件（利冲隔离）
      allow —— 某个案件只有白名单内的人可见（涉密案件）
    """

    #: 明确禁止我访问的资源 id
    denied_ids: FrozenSet[str] = frozenset()
    #: 设了白名单的资源 id（只要设了，名单外的人一律不可见）
    restricted_ids: FrozenSet[str] = frozenset()
    #: 上述白名单里我在其中的资源 id
    allowed_ids: FrozenSet[str] = frozenset()

    def blocks(self, resource_id: str) -> bool:
        rid = str(resource_id)
        if rid in self.denied_ids:
            return True
        return rid in self.restricted_ids and rid not in self.allowed_ids

    @property
    def is_empty(self) -> bool:
        return not self.denied_ids and not self.restricted_ids


EMPTY_WALL = WallView()


# ---------------------------------------------------------------- 主体


@dataclass(frozen=True)
class Principal:
    """当前请求者的权限快照。由 identity 模块在每次请求时装配。

    内核不知道角色是怎么配的，只认这份快照 —— 这样换权限模型不影响业务代码。
    """

    user_id: int
    tenant_id: int
    is_superuser: bool = False
    permissions: FrozenSet[str] = frozenset()
    #: 权限点 -> 该权限点上最宽的数据范围
    scopes: Mapping[str, str] = field(default_factory=dict)
    #: 我直属的组织
    org_ids: Sequence[int] = ()
    #: 我所属组织及其全部下级
    org_tree_ids: Sequence[int] = ()
    #: CUSTOM 范围授权时指定的组织
    custom_org_ids: Sequence[int] = ()

    def can(self, permission: str) -> bool:
        return self.is_superuser or permission in self.permissions

    def scope_for(self, permission: str) -> str:
        if self.is_superuser:
            return DataScope.ALL
        return self.scopes.get(permission, DataScope.SELF)


def require(principal: Principal, permission: str) -> None:
    """功能权限检查。没有就 403。"""
    if not principal.can(permission):
        raise PermissionDenied(
            "缺少权限：{0}".format(permission), code="security.permission_denied"
        )


# ---------------------------------------------------------------- 数据范围过滤


def scope_org_ids(principal: Principal, scope: str) -> Sequence[int]:
    if scope == DataScope.DEPT:
        return principal.org_ids
    if scope == DataScope.DEPT_TREE:
        return principal.org_tree_ids
    if scope == DataScope.CUSTOM:
        return principal.custom_org_ids
    return ()


def apply_scope(
    stmt: Select,
    principal: Principal,
    permission: str,
    *,
    owner_col: Any = None,
    org_col: Any = None,
    tenant_col: Any = None,
) -> Select:
    """给查询加上数据范围过滤。**所有模块的列表查询都必须过这一层。**

    owner_col  记录归属人（SELF 范围用）
    org_col    记录归属组织（DEPT / DEPT_TREE / CUSTOM 范围用）
    tenant_col 租户列，传了就一并过滤
    """
    require(principal, permission)

    if tenant_col is not None:
        stmt = stmt.where(tenant_col == principal.tenant_id)

    scope = principal.scope_for(permission)
    if scope == DataScope.ALL:
        return stmt

    if scope == DataScope.SELF:
        if owner_col is None:
            raise PermissionDenied(
                "该查询未提供归属人列，无法按 SELF 范围过滤",
                code="security.scope_misconfigured",
            )
        return stmt.where(owner_col == principal.user_id)

    org_ids = list(scope_org_ids(principal, scope))
    if org_col is None:
        raise PermissionDenied(
            "该查询未提供归属组织列，无法按 {0} 范围过滤".format(scope),
            code="security.scope_misconfigured",
        )
    if not org_ids:
        # 没有任何组织归属 —— 退化为只看自己的，而不是看到全部
        if owner_col is not None:
            return stmt.where(owner_col == principal.user_id)
        return stmt.where(org_col.in_([-1]))

    condition = org_col.in_(org_ids)
    if owner_col is not None:
        # 自己经办的记录，即使不在范围内的组织也应可见
        condition = or_(condition, owner_col == principal.user_id)
    return stmt.where(condition)


def apply_wall(stmt: Select, id_col: Any, wall: WallView) -> Select:
    """给查询加上隔离墙过滤。**在 apply_scope 之后再套一层，优先级更高。**"""
    if wall.is_empty:
        return stmt
    conditions = []
    if wall.denied_ids:
        conditions.append(~id_col.in_([str(x) for x in wall.denied_ids]))
    if wall.restricted_ids:
        visible = [str(x) for x in wall.allowed_ids]
        restricted = [str(x) for x in wall.restricted_ids]
        conditions.append(
            or_(~id_col.in_(restricted), id_col.in_(visible)) if visible
            else ~id_col.in_(restricted)
        )
    return stmt.where(and_(*conditions)) if conditions else stmt


def assert_not_walled(wall: WallView, resource_type: str, resource_id: Any) -> None:
    """单条访问时的隔离墙检查。

    注意措辞：不能提示"这条被隔离了"，否则等于泄露了案件存在这一事实。
    """
    if wall.blocks(resource_id):
        raise PermissionDenied(
            "无权访问该{0}".format(resource_type), code="security.walled"
        )


# ---------------------------------------------------------------- 越权访问识别


def is_cross_scope(principal: Principal, permission: str, owner_id: Optional[int]) -> bool:
    """本次访问是否超出了"仅本人"的范围 —— 用于决定要不要写审计。

    主任查全所是合法的，但**必须留痕**，这是律所合规检查会看的东西。
    """
    if owner_id is not None and owner_id == principal.user_id:
        return False
    return principal.scope_for(permission) != DataScope.SELF
