"""case 模块的对外唯一出口。

Step 7 的利冲检查会这样用：

    case_api = registry.api("case")
    hits = case_api.cases_of_party(session, tenant_id, related_ids, ours=False)
    # -> 这些主体在所里哪些案子里当过对方
"""
from modules.case.service import (
    metrics,
    archive_inventory,
    archive_blockers,
    ALLOWED_TRANSITIONS,
    OPEN_STATUSES,
    STATUS_LABELS,
    add_member,
    add_party,
    assert_visible,
    FROZEN_STATUSES,
    assert_writable,
    scoped_cases,
    wall_of,
    can_transition,
    case_ids_of_member,
    cases_of_party,
    create_case,
    create_cause,
    create_matter,
    ensure_default_causes,
    get_case,
    get_cause,
    list_causes,
    matters_of,
    members_of,
    parties_of,
    reassign_lead,
    remove_member,
    set_matter_status,
    status_history,
    transition,
)

__all__ = [
    # 看板指标（Step 21）——各模块自报，analytics 只汇总
    "metrics",
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory", "archive_blockers",
    # 案由字典
    "create_cause", "get_cause", "list_causes", "ensure_default_causes",
    # 案件与状态机
    "create_case", "get_case", "transition", "status_history",
    "can_transition", "ALLOWED_TRANSITIONS", "OPEN_STATUSES", "STATUS_LABELS",
    "assert_visible", "scoped_cases", "wall_of",
    # ★ 写操作走这个：看得见 **且** 写得动（归档/终止的案子只读）
    "assert_writable", "FROZEN_STATUSES",
    "reassign_lead",
    # 当事人（利冲的数据源）
    "add_party", "parties_of", "cases_of_party",
    # 承办人
    "add_member", "remove_member", "members_of", "case_ids_of_member",
    # 事项
    "create_matter", "matters_of", "set_matter_status",
]
