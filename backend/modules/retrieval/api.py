"""retrieval 模块的对外唯一出口。

Step 10 的案件流程图会这样用：

    r = registry.api("retrieval")
    log = r.submit(session, tenant, actor, "answer", question, case_id=case.id)
    # …轮询…
    citations = r.citations_of(session, log.id)   # -> Step 13 引用留痕
"""
from modules.retrieval.service import (
    archive_inventory,
    build_case_context,
    citations_of,
    cost_of_tenant,
    get_log,
    health,
    logs_of_case,
    poll,
    search,
    submit,
)

__all__ = [
    # 归档：卷宗目录 / 归档前的阻断检查（archive 模块来问）
    "archive_inventory",
    # 纯检索（0 成本、秒级）
    "search",
    # 长任务（有成本、任务化）
    "submit", "poll",
    # 留档查询
    "get_log", "citations_of", "logs_of_case",
    # 案件上下文（Step 10 流程图也会用）
    "build_case_context",
    # 运维
    "cost_of_tenant", "health",
]
