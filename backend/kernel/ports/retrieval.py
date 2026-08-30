"""法律检索契约 —— 对接独立运行的法律检索服务（Step 8）。

关键约定：

  1. **citations 必须结构化。** 文书留痕、引用验证、审计全靠它，
     只给一段总结文字是不够的。

  2. **source_id 必须是稳定标识，不是 chunk_id。**
     检索库会重建（换数据源、重新切分、扩充法条），chunk_id 会重排。
     引用如果绑在 chunk_id 上，库一重建，**所有历史文书的引用都会指向
     错误内容** —— 比没有引用还糟。

         法条      法释〔2020〕25号#第十四条
         指导案例   指导案例7号
         裁判文书   (2026)鲁02民初123号

     chunk_id 只放 locator，作"当时的定位"，仅供加速，不作身份。

  3. **结果带 corpus_version。** 平台连同引用一起存下来，
     将来能回答"这条引用是哪个版本的库检出来的"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@dataclass
class Citation:
    """一条可回溯的引用。"""

    source_type: str  # law | case | regulation | internal
    #: ★ 稳定标识，**不是 chunk_id**。库重建后必须仍能定位到同一条内容
    source_id: str
    title: str
    #: 原文片段，**不是摘要** —— 引用校验与文书留痕都要看原文
    snippet: str
    url: str = ""
    #: 当时的定位（chunk_id 等）。仅供加速，不作身份
    locator: str = ""
    #: 现行有效（2021-01-01 施行）/ 已失效（法〔2026〕119号）/ 未标注
    effective_status: str = ""


@dataclass
class RetrievalItem:
    title: str
    summary: str
    score: float = 0.0
    citation: Optional[Citation] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseContext:
    """检索时注入的案件上下文 —— **有没有它，检索质量差别很大。**

    检索服务会把这些要素喂进改写节点，围绕它们生成检索式。
    实测：同一个问题带上"已完工、已实际使用、未验收"，
    改写出的检索式直接命中建工解释一第十四条；不带就是一堆泛泛的工程款条文。
    """

    case_id: str = ""
    cause_of_action: str = ""  # 案由
    facts: str = ""
    disputes: List[str] = field(default_factory=list)  # 争议焦点
    opponent_claims: str = ""


@dataclass
class RetrievalRequest:
    query: str
    case_context: Optional[CaseContext] = None
    scope: List[str] = field(default_factory=lambda: ["law", "case"])
    filters: Dict[str, Any] = field(default_factory=dict)
    top_k: int = 10


@dataclass
class RetrievalResult:
    items: List[RetrievalItem] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)
    answer: str = ""
    trace_id: str = ""
    #: 语料版本指纹。库重建后能区分"这条引用出自哪个版本"
    corpus_version: str = ""
    #: 花费与调用次数，按租户记账用
    cost: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RetrievalPort(Protocol):
    """纯检索：0 成本、秒级、结果确定、可缓存。

    不走改写，所以拿不到案情要素的加成。要让要素真正影响结果，用 LegalQAPort。
    """

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        ...

    def lookup(self, source_ids: List[str]) -> Dict[str, Any]:
        """按引用标识反查现状 —— 给「引用体检」用。

        ★ 只读、0 成本。回答的是「这条引用现在的库里还是那样吗」，
          **不回答「应该改成什么」** —— 那是律师的判断。

        返回 {"items": {source_id: {...}}, "corpus_version": "..."}
        找不到的条目返回 {"found": False}，**不是报错** ——
        库重建、法条下架都会让老引用查不到，那是正常情况。
        """
        ...

    def health(self) -> Dict[str, Any]:
        """检索服务是否可用、当前语料版本。"""
        ...
