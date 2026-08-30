"""conflict 模块的对外唯一出口。

case 模块通过**可选依赖**用它卡住流程：

    conflict = registry.optional_api("conflict")
    if conflict is not None:
        conflict.assert_cleared(session, case_id)
    # 没装 conflict 模块就放行 —— 系统降级运行，不是崩溃
"""
from modules.conflict.service import (
    archive_inventory,
    PASSING_STATUSES,
    assert_cleared,
    checks_of_case,
    decide,
    get_check,
    hits_of,
    is_cleared,
    latest_check,
    resolve_hit,
    run_check,
)

__all__ = [
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory",
    "run_check", "decide", "resolve_hit",
    "get_check", "hits_of", "checks_of_case", "latest_check",
    "is_cleared", "assert_cleared", "PASSING_STATUSES",
]
