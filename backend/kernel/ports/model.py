"""大模型契约。实现见 adapters/model/（DeepSeek）。

业务代码永远不 import 任何厂商 SDK。切换 DeepSeek API / 私有化部署
只改配置 LFP_MODEL_BACKEND。

注意：**向量化不在这个 Port 里**。DeepSeek 不提供 embeddings 接口，
且 D2 规定向量必须本地跑（案情不出境）。见 kernel/ports/embedding.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:  # Python 3.8+
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class ModelResult:
    text: str
    # 结构化输出（要求 json_schema 时填充，已按该 schema 校验通过）
    data: Optional[Dict[str, Any]] = None
    # 思考过程（thinking=True 时可能返回）
    reasoning: Optional[str] = None
    # tokens 与估算花费，按租户记账用
    usage: Dict[str, Any] = field(default_factory=dict)
    model: str = ""
    trace_id: str = ""


@runtime_checkable
class ModelPort(Protocol):
    """全系统唯一的对话模型出口。"""

    def chat(
        self,
        messages: List[ChatMessage],
        *,
        json_schema: Optional[Dict[str, Any]] = None,
        thinking: bool = False,
        effort: str = "high",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> ModelResult:
        """一次对话补全。

        json_schema  传入后强制 JSON 输出，并按该 schema 校验（失败会重试一次）
        thinking     开启思考模式；律师业务里的定性/推理类任务才需要
        effort       low | high | max，仅在 thinking=True 时有意义
        model        留空用默认档位；难任务传更强的型号
        """
        ...
