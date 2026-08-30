"""待办与时限。规则是数据不是代码；算不出来要明说。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, Metric, ModuleManifest


class CaseParams(BaseModel):
    case_id: int = Field(description="案件 id")


class BoardParams(BaseModel):
    user_id: int = Field(default=0, description="看谁的待办，0 表示自己")


MANIFEST = ModuleManifest(
    name="task",
    version="0.1.0",
    description="待办与时限：规则表驱动、算不出来明确标注、除斥期间不许中断",
    requires=("identity", "case"),
    optional=(),
    #: ★ 本模块有等人动手的事，报进统一待办中心。
    #:   实现在 api.inbox_items() —— 待办跟人走，没法静态声明。
    has_inbox=True,
    permissions=(
        "task.view",         # 看待办与时限
        "task.manage",       # 建任务、改任务
        "task.deadline",     # 确认起算日、登记中断、标记已行使
        "task.manage_rule",  # 维护时限规则（律所自定义）
    ),
    metrics=(
        # ★ 存量不用比率：达成率一挂墙，最快的提分法是把时限标成「不适用」
        # ★ drill **必须带上筛选**。第一版四个都指 "/todo"，
        #   点哪个格子都是同一张全量表 —— 那这个格子就白点了：
        #   你从"12 条算不出"点进去，还得自己在几十行里再找一遍那 12 条。
        #   **点开之后看到的必须正好是这个数字本身。**
        Metric(key="task.expired", name="已过期时限", unit="count",
               description="上诉期、诉讼时效这类**过了就失权**的期限（不含法院审限）",
               scope_perm="task.view", drill="/todo?f=expired"),
        Metric(key="task.urgent", name="临近时限", unit="count",
               description="还剩不到 5 天的",
               scope_perm="task.view", drill="/todo?f=urgent"),
        Metric(key="task.unknown", name="算不出的时限", unit="count",
               description="缺起算日 —— 这些没人在盯",
               scope_perm="task.view", drill="/todo?f=unknown"),
        Metric(key="task.overdue", name="逾期待办", unit="count",
               description="自己排的活过了期，和法定时限不是一回事",
               scope_perm="task.view", drill="/todo?f=overdue"),
    ),
    capabilities=(
        Capability(
            name="task.deadlines",
            description="查一个案件的时限清单",
            params=CaseParams,
            requires_perm="task.view",
            read_only=True,
        ),
        Capability(
            name="task.board",
            description="我的待办：任务 + 时限",
            params=BoardParams,
            requires_perm="task.view",
            read_only=True,
        ),
    ),
)
