"""consult 模块的对外唯一出口。

★ `billable` / `mark_billed` 是留给 billing 的：出账时它要把咨询捞出来，
  但**不能碰 consult 的表**，只能走这里。
  「什么样的咨询能进账单」这条规则归 consult 自己判 ——
  billing 不该知道 free / draft / void 这些状态是怎么回事。
"""
from modules.consult.service import (
    CHANNEL_LABELS,
    STATUS_LABELS,
    archive_blockers,
    archive_inventory,
    billable,
    billable_minutes,
    by_client,
    describe,
    list_consults,
    log,
    mark_billed,
    metrics,
    rate_for,
    update,
    void,
)

__all__ = [
    # 归档：卷宗目录 / 归档前提醒（archive 模块来问）
    "archive_inventory", "archive_blockers",
    # 记录
    "log", "update", "void",
    # 查询
    "list_consults", "by_client",
    # 给 billing 用：什么样的咨询能进账单，这条规则归 consult
    "billable", "mark_billed",
    # 看板
    "metrics",
    # 展示
    "describe", "rate_for", "billable_minutes",
    "CHANNEL_LABELS", "STATUS_LABELS",
]
