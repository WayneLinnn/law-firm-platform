"""对外契约（Ports）。业务模块只依赖这里的接口，永远不 import adapters。

换掉任何一个外部实现（模型、检索引擎、编排引擎、对象存储），
业务代码一行都不用改 —— 这是本项目模块化的核心收益。
"""
from kernel.ports.embedding import EmbeddingPort
from kernel.ports.events import Event, EventPort
from kernel.ports.legal_qa import (
    Answer,
    CaseComparison,
    Elements,
    LegalQAPort,
    QAJob,
)
from kernel.ports.model import ChatMessage, ModelPort, ModelResult
from kernel.ports.retrieval import (
    CaseContext,
    Citation,
    RetrievalItem,
    RetrievalPort,
    RetrievalRequest,
    RetrievalResult,
)
from kernel.ports.storage import StoragePort, StoredFile
from kernel.ports.workflow import WorkflowPort, WorkflowRun

__all__ = [
    # 模型与向量（刻意分开：DeepSeek 无 embeddings，且向量必须本地跑）
    "ChatMessage", "ModelPort", "ModelResult", "EmbeddingPort",
    # 纯检索（0 成本、确定性、可缓存）
    "CaseContext", "Citation", "RetrievalItem",
    "RetrievalPort", "RetrievalRequest", "RetrievalResult",
    # 法律问答（有成本、有 trace、任务化）
    "LegalQAPort", "QAJob", "Answer", "Elements", "CaseComparison",
    # 流程编排
    "WorkflowPort", "WorkflowRun",
    # 对象存储
    "StoragePort", "StoredFile",
    # 事件（预留）
    "Event", "EventPort",
]
