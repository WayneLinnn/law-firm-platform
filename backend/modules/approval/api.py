"""approval 模块的对外唯一出口。

★ `check_subject_leak` 导出来是给别的模块用的：将来 doc 模块往
  单据上挂附件时，附件名同样会跟着单子走，同一道检查该复用。
"""
from modules.approval.service import (
    inbox_items,
    metrics,
    archive_inventory,
    archive_blockers,
    cancel,
    check_subject_leak,
    create_flow,
    create_ticket,
    decide,
    describe,
    describe_flow,
    describe_step,
    ensure_default_flows,
    execute_seal,
    flow_for,
    inbox,
    list_flows,
    my_tickets,
    parse_steps,
    pay,
    steps_of,
    submit,
    tickets_of_case,
)

__all__ = [
    # 统一待办中心来问的：本模块有什么等人动手的事
    "inbox_items",
    # 看板指标（Step 21）——各模块自报，analytics 只汇总
    "metrics",
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory", "archive_blockers",
    # 流转规则 —— 规则即数据
    "ensure_default_flows", "list_flows", "create_flow", "flow_for", "parse_steps",
    # ★ 隔离墙：拦住把案情写进单据
    "check_subject_leak",
    # 单据
    "create_ticket", "submit", "decide", "pay", "execute_seal", "cancel",
    # 查询
    "my_tickets", "inbox", "tickets_of_case", "steps_of",
    # 展示 —— ★ describe 会按查看者的案件可见性做脱敏
    "describe", "describe_step", "describe_flow",
]
