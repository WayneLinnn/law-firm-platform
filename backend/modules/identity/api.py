"""identity 模块的对外唯一出口。

其他模块要判断"当前用户是谁""某人属于哪些组织"，只能从这里拿：
    from kernel.registry import registry
    identity = registry.api("identity")
    org_ids = identity.org_ids_of_user(session, user_id)
"""
from modules.identity.access import (
    assign_role,
    build_principal,
    clear_wall,
    remove_wall,
    create_role,
    ensure_default_roles,
    get_role,
    list_roles,
    revoke_role,
    role_permissions,
    roles_of,
    set_role_permissions,
    set_wall,
    walls_on,
    wall_view,
)
from modules.identity.deps import (
    client_ip,
    current_principal,
    current_user,
    current_user_id,
    optional_user,
    require_perm,
)
from modules.identity.service import (
    appoint,
    appointment_history,
    assign_membership,
    authenticate,
    committee_members,
    create_org_unit,
    create_position,
    create_tenant,
    create_user,
    decode_token,
    descendant_ids,
    end_membership,
    ensure_default_positions,
    find_user,
    get_org_unit,
    get_position,
    get_tenant,
    get_user,
    is_committee_member,
    issue_token,
    leader_of,
    list_org_units,
    list_positions,
    members_of,
    memberships_of,
    move_org_unit,
    org_ids_of_user,
    license_of,
    position_of,
    set_license,
    teams_of,
)

__all__ = [
    # 认证
    "authenticate", "issue_token", "decode_token",
    "current_user", "current_user_id", "optional_user",
    # 权限（业务模块从这里拿，不要自己实现）
    "current_principal", "require_perm", "build_principal", "client_ip",
    "create_role", "ensure_default_roles", "list_roles", "get_role",
    "set_role_permissions", "role_permissions",
    "assign_role", "revoke_role", "roles_of",
    # 隔离墙
    "set_wall", "clear_wall", "wall_view", "walls_on", "remove_wall",
    # 租户
    "create_tenant", "get_tenant",
    # 组织
    "create_org_unit", "get_org_unit", "list_org_units",
    "descendant_ids", "move_org_unit",
    # 用户
    "create_user", "get_user", "find_user",
    # 职级与任职（晋升有历史）
    "create_position", "ensure_default_positions", "list_positions", "get_position",
    "appoint", "position_of", "appointment_history",
    "license_of", "set_license",
    # 归属与团队
    "assign_membership", "end_membership",
    "memberships_of", "members_of", "org_ids_of_user",
    "leader_of", "teams_of",
    # 管委会（权力机构）
    "committee_members", "is_committee_member",
]
