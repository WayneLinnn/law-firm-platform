"""task 模块的对外唯一出口。

★ `generate` 是给 workflow 用的：律师确认过的要素 → 落成案件上的时限。
  这一步把「跑完就没」变成「长期挂在案件上」。
"""
from modules.task.service import (
    inbox_items,
    metrics,
    archive_inventory,
    archive_blockers,
    FIELD_LABELS,
    add_deadline,
    board,
    create_rule,
    create_task,
    deadlines_of,
    describe,
    ensure_default_rules,
    generate,
    interrupt,
    suspend,
    list_rules,
    mark,
    set_anchor,
    set_rule_active,
    tasks_of_case,
    update_task,
)

__all__ = [
    # 统一待办中心来问的：本模块有什么等人动手的事
    "inbox_items",
    # 看板指标（Step 21）——各模块自报，analytics 只汇总
    "metrics",
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory", "archive_blockers",
    "ensure_default_rules", "list_rules", "create_rule", "set_rule_active",
    "generate", "deadlines_of", "set_anchor", "interrupt", "suspend", "mark",
    "add_deadline", "describe", "FIELD_LABELS",
    "create_task", "update_task", "tasks_of_case", "board",
]
