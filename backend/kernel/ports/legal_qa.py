"""法律问答契约 —— 与 RetrievalPort 刻意拆开。

为什么拆（手册 C2）：
    RetrievalPort  纯检索：0 成本、确定性、可缓存、可随便重跑
    LegalQAPort    问答/要素抽取/类案对比：有模型调用、有成本、有 trace、必须留痕

两者的计费、缓存、重试策略完全不同，混在一个 Port 里将来做配额和成本
控制会非常难受。

另一条硬约束（C3）：这些操作耗时长（实测问答 15-26 秒、要素抽取 43 秒），
所以一律**任务化** —— 提交拿 job_id，再轮询取结果，不做同步阻塞调用。

还有（C1）：人工确认只允许存在于案件流程图那一层。本 Port 的实现必须
无状态、不挂起 —— 检索服务侧一律以 human=False 运行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from kernel.ports.retrieval import CaseContext, Citation, RetrievalItem

#: 任务类型
QA_KINDS = ("answer", "extract", "compare")


@dataclass
class QAJob:
    """一次长任务的句柄。"""

    job_id: str
    kind: str  # answer | extract | compare
    status: str  # queued | running | done | failed
    progress: str = ""
    error: str = ""
    elapsed: float = 0.0

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed")


@dataclass
class Answer:
    """问答结果。"""

    text: str
    citations: List[Citation] = field(default_factory=list)
    items: List[RetrievalItem] = field(default_factory=list)
    #: 答复实际引用到的 S 编号 —— 文书留痕只需要这些
    used: List[int] = field(default_factory=list)
    #: 引用校验告警：编造条号、查无此案、已废止…
    warnings: List[str] = field(default_factory=list)
    #: 检索服务改写出的检索式，便于律师理解"它到底查了什么"
    queries: List[str] = field(default_factory=list)
    intent: str = ""
    trace_id: str = ""
    corpus_version: str = ""
    cost: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Elements:
    """案情要素抽取结果。"""

    cause_of_action: str = ""  # 案由
    legal_relation: str = ""
    parties: List[str] = field(default_factory=list)
    key_facts: List[str] = field(default_factory=list)
    disputes: List[str] = field(default_factory=list)  # 争议焦点
    amount: str = ""
    timeline: List[str] = field(default_factory=list)
    #: 律师还需补充的关键事实
    missing: List[str] = field(default_factory=list)
    trace_id: str = ""
    cost: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseComparison:
    """类案对比表。"""

    rows: List[Dict[str, str]] = field(default_factory=list)
    conclusion: str = ""
    citations: List[Citation] = field(default_factory=list)
    trace_id: str = ""
    cost: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LegalQAPort(Protocol):
    """长任务一律 submit → poll → result，不做同步阻塞。"""

    def submit_answer(
        self,
        question: str,
        case_context: Optional[CaseContext] = None,
        history: Optional[List[Any]] = None,
        top_k: int = 8,
    ) -> QAJob:
        ...

    def submit_extract(
        self, text: str, case_context: Optional[CaseContext] = None
    ) -> QAJob:
        ...

    def submit_compare(
        self, question: str, case_context: Optional[CaseContext] = None
    ) -> QAJob:
        ...

    def poll(self, job_id: str) -> QAJob:
        ...

    def result(self, job_id: str) -> Any:
        """任务完成后取结果：Answer / Elements / CaseComparison。"""
        ...

    def drop(self, job_id: str) -> None:
        """取完结果后清理，避免服务端内存增长。"""
        ...
