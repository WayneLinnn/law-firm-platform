"""workflow 模块的对外唯一出口。

其他模块想跑流程，只能通过这里 —— 不要直接 import service 或 models。
"""
from modules.workflow.service import (
    archive_inventory,
    FINAL_STATUSES,
    available_templates,
    describe_templates,
    cancel,
    get_run,
    live_state,
    resume,
    runs_of_case,
    start,
    steps_of,
)

__all__ = [
    # 归档：卷宗目录（archive 模块来问）
    "archive_inventory",
    "start", "resume", "cancel",
    "get_run", "runs_of_case", "steps_of", "live_state",
    "available_templates", "describe_templates", "FINAL_STATUSES",
]
