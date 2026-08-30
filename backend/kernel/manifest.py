"""模块清单。每个模块必须在 manifest.py 里定义一个 MANIFEST 常量。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Type

from pydantic import BaseModel


#: 待办的紧急度。**排序就是这个顺序**，而且顺序本身是个业务判断：
#:
#: ★ `unknown`（算不出）排在 `urgent`（临近）**前面**。
#:   临近的期限至少系统在替你盯着；算不出的那些**没人在盯** ——
#:   它缺起算日，谁也不知道还剩几天。
#:   一个看起来干净的待办列表如果藏着三条算不出的除斥期间，它就是在骗人。
INBOX_URGENCY = ("expired", "unknown", "urgent", "normal")


@dataclass(frozen=True)
class InboxItem:
    """一件**等我动手**的事。

    ★ **各模块自报，待办中心只汇总。** 这是本项目第五次用这个模式
      （capabilities / archive_inventory / archive_blockers / metrics / inbox）。

      不这么做的话，待办中心就要去依赖时限、单据、账单…… 加一个模块
      改一次；反过来 —— **加一个模块 = 待办中心自动多一类事，它一行不改。**

    ★ 为什么要有这个东西：待办原来散在三个地方 ——
      待办页有时限、单据页有「待我处理」、工作台还有一排格子。
      律师早上要点三个地方才知道今天有什么会出事，**于是就不点了**。

    ★ 什么算 InboxItem：**等着我做一个动作**的东西。
      「这个案子欠了 20 万」不是 —— 那是状态，不是待办。
      「这张单等我批」是。分不清的话，问一句：
      **做完之后它会从列表里消失吗？** 不会就不是待办。
    """

    #: 全局唯一。形如 "task.deadline:123" —— 前缀是模块，冒号后是模块内的 id
    key: str
    #: 标题：一句话说清要做什么
    title: str
    #: 哪一类。界面按它分组、给图标
    kind: str = "task"
    #: 补充说明（法条依据、金额、提单人…）
    detail: str = ""
    #: 到期日 ISO 串。**空串表示算不出** —— 不是"没有期限"
    due_on: str = ""
    #: 见 INBOX_URGENCY
    urgency: str = "normal"
    #: 点开去哪。★ 没有 link 的待办等于没有 —— 人看到了却不知道去哪做
    link: str = ""
    #: 关联案件（可空 —— 单据是刻意不挂案件的）
    case_id: Optional[int] = None
    #: 为什么这件事轮到我。★ 律所里"谁该做"经常不明显，
    #: 尤其是按权限而不是按指派算的单据
    why_me: str = ""


@dataclass(frozen=True)
class Metric:
    """一个可以上看板的指标。

    ★ **各模块自报，analytics 只汇总。** 理由是依赖方向：
      指标要跨案件、账单、时限、单据 —— 让 analytics 去依赖所有模块，
      加一个模块就要改一次 analytics；反过来让各模块自报，
      **加一个模块 = 看板自动多几个指标，analytics 一行不改。**

      这是本项目第四次用这个模式（capabilities / archive_inventory /
      archive_blockers / metrics）—— 已经是这套架构的固定手法。

    ★ `unit` 决定界面怎么显示，也决定**要不要给它配一个入口**：
      count/money 这类必须能点开到明细，**点不开的数字是装饰**。

    ★ `scope_perm` 是"看这个指标要什么权限"。数据范围的过滤在各模块
      自己的实现里做（它们本来就在做），这里只管"能不能看见这一格"。
    """

    key: str
    name: str
    #: count 件数 / money 金额 / ratio 比率 / days 天数
    unit: str = "count"
    description: str = ""
    scope_perm: str = ""
    #: 点开去哪 —— 前端据此把数字变成链接。空表示这个数点不开
    drill: str = ""


@dataclass(frozen=True)
class Capability:
    """一个可以被 Agent 调用的动作。

    【现在只声明，不消费】—— 将来接 Agent（OpenClaw / 飞书 / 其他）时，
    把所有模块的 capabilities 汇总起来就是一份 MCP tool 清单，一步遍历即可导出。
    见开发手册第 10 章。

    形状刻意对齐 MCP tool schema：
        name         -> tool name
        description  -> tool description
        params       -> Pydantic 模型，.model_json_schema() 就是 inputSchema
        requires_perm-> 调用前必须持有的权限点（走 Step 4 的 DataScope）
        read_only    -> 写操作必须显式置 False，且要二次确认 + 审计
    """

    name: str
    description: str
    params: Optional[Type[BaseModel]] = None
    requires_perm: str = ""
    read_only: bool = True

    def input_schema(self) -> Dict[str, Any]:
        """导出 MCP tool 的 inputSchema。Pydantic 原生支持，零翻译成本。"""
        if self.params is None:
            return {"type": "object", "properties": {}}
        return self.params.model_json_schema()

    def as_mcp_tool(self) -> Dict[str, Any]:
        """将来生成 MCP server 时直接用这个。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema(),
        }


@dataclass(frozen=True)
class ModuleManifest:
    """描述一个业务模块。

    name         模块名，同时是 Python 包名和 Postgres schema 名
    requires     必需依赖；缺失则整个系统拒绝启动（暴露错误配置）
    optional     可选依赖；缺失时本模块降级运行，系统照常启动
    permissions  本模块注册的权限点（Step 4 由 identity 消费）
    capabilities 本模块暴露给 Agent 的动作（第 10 章，现在只声明）
    metrics      本模块能往看板上报的指标（Step 21）
    """

    name: str
    version: str = "0.1.0"
    description: str = ""
    requires: Tuple[str, ...] = field(default_factory=tuple)
    optional: Tuple[str, ...] = field(default_factory=tuple)
    permissions: Tuple[str, ...] = field(default_factory=tuple)
    capabilities: Tuple[Capability, ...] = field(default_factory=tuple)
    #: 本模块能往看板上报的指标（Step 21）。声明在这里，实现在 api.metrics()
    metrics: Tuple[Metric, ...] = field(default_factory=tuple)
    #: 本模块**有没有等人动手的事**要报进统一待办中心。
    #: 只是一个开关 —— 具体列表实现在 api.inbox_items(session, principal)，
    #: 因为待办是跟人走的，没法像 Metric 那样静态声明。
    has_inbox: bool = False

    # 路由挂载前缀，默认 /api/<name>
    url_prefix: str = ""

    @property
    def schema(self) -> str:
        """本模块独占的 Postgres schema。删模块 = 删这个 schema。"""
        return self.name

    @property
    def prefix(self) -> str:
        return self.url_prefix or "/api/{0}".format(self.name)
