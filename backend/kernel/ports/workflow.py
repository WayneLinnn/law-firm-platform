"""流程编排契约 —— 实现用 LangGraph（Step 9）。

上层永远不 import langgraph。将来换编排引擎，业务代码零改动。

**为什么流程模板长成 Step 列表而不是一张图**（Step 10 定的）：

铁律是「adapters 不得 import modules」。如果图写在适配器里，
建工的业务规则（时限怎么算、争议焦点怎么拆）就得跟着写进适配器 ——
律所的业务知识落在一个本该只管"怎么跑图"的地方，换引擎时还得搬一遍。

所以反过来：**业务模块声明「这条流程有哪几步、每步干什么」，
适配器负责把它编译成图。** 业务侧一行 langgraph 都不用碰。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@dataclass
class WorkflowRun:
    run_id: str
    template_key: str
    case_id: str
    status: str  # running | waiting_human | done | failed
    current_node: str = ""
    state: Dict[str, Any] = field(default_factory=dict)
    pending_question: Optional[str] = None


@dataclass
class Step:
    """流程里的一步。

    一步最多做三件事，按顺序：

        run    自动执行。拿当前 state，返回要合并进去的增量。
        ask    要不要停下来问律师。返回问题文本；返回空串就不停。
        apply  律师答完之后。拿 (state, 答复文本, 结构化修正)，返回增量。

    ★ 三个回调都必须返回**可 JSON 序列化**的 dict —— 它们要进 checkpoint。
      数据库 session、httpx client 这类东西绝不能塞进 state，
      否则跨进程恢复就废了（这是整个 Step 9 的立身之本）。
    """

    key: str
    title: str
    run: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    ask: Optional[Callable[[Dict[str, Any]], str]] = None
    apply: Optional[Callable[[Dict[str, Any], str, Dict[str, Any]], Dict[str, Any]]] = None


@dataclass
class Template:
    """一条流程模板。目前是线性的 —— 真实办案流程本来就是一步接一步。

    需要分支时再加，不要提前设计（手册第 7 章「有意留白」）。
    """

    key: str
    label: str
    steps: List[Step] = field(default_factory=list)
    #: 发起时必须带的字段，缺了就别让它跑起来
    requires: tuple = ()


@runtime_checkable
class WorkflowPort(Protocol):
    def templates(self) -> List[str]:
        """可用的流程模板 key（一个案由一张图）。"""
        ...

    def register(self, template: Template) -> None:
        """登记一条业务流程模板。由业务模块在装载时调用。"""
        ...

    def start(self, template_key: str, case_id: str, payload: Dict[str, Any]) -> WorkflowRun:
        ...

    def resume(self, run_id: str, human_input: Dict[str, Any]) -> WorkflowRun:
        """人工确认后继续 —— 对应 LangGraph 的 interrupt。"""
        ...

    def get(self, run_id: str) -> WorkflowRun:
        ...
