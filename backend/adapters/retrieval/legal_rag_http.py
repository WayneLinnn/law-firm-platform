"""法律检索服务的 HTTP 适配器 —— 同时实现 RetrievalPort 与 LegalQAPort。

对面是一个独立进程（`langchain-legal-RAG` 的 `src/service.py`），
不是 in-process import。边界是刻意的：

  · torch + bge 模型加载慢，不能拖累主应用重启
  · 那边有爬虫、建库、向量化等长任务，生命周期与业务应用无关
  · **那个库要大改**（换数据源、重新切分、扩充法条），
    HTTP 契约把平台和它的内部结构隔开 —— 它怎么重建，平台一行不改

只有 kernel/bootstrap.py 可以 import 本文件。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from kernel.config import Settings
from kernel.errors import DomainError
from kernel.ports.legal_qa import Answer, CaseComparison, Elements, QAJob
from kernel.ports.retrieval import (
    CaseContext,
    Citation,
    RetrievalItem,
    RetrievalRequest,
    RetrievalResult,
)

logger = logging.getLogger(__name__)


def _ctx(context: Optional[CaseContext]) -> Optional[Dict[str, Any]]:
    if context is None:
        return None
    return {
        "case_id": str(context.case_id or ""),
        "cause_of_action": context.cause_of_action,
        "facts": context.facts,
        "disputes": list(context.disputes),
        "opponent_claims": context.opponent_claims,
    }


def _citation(raw: Dict[str, Any]) -> Citation:
    return Citation(
        source_type=raw.get("source_type", ""),
        source_id=raw.get("source_id", ""),
        title=raw.get("title", ""),
        snippet=raw.get("snippet", ""),
        url=raw.get("url", ""),
        locator=raw.get("locator", ""),
        effective_status=raw.get("effective_status", ""),
    )


def _item(raw: Dict[str, Any]) -> RetrievalItem:
    citation = raw.get("citation")
    return RetrievalItem(
        title=raw.get("title", ""),
        summary=raw.get("summary", ""),
        score=float(raw.get("score") or 0.0),
        citation=_citation(citation) if citation else None,
        meta=raw.get("meta") or {},
    )


class LegalRagHttp:
    """实现 kernel.ports.RetrievalPort 与 kernel.ports.LegalQAPort。"""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        self._base = settings.legal_rag_url.rstrip("/")
        headers = {}
        if settings.legal_rag_token:
            # 服务间认证。检索服务侧设了 RAG_SERVICE_TOKEN 就必须带
            headers["Authorization"] = "Bearer " + settings.legal_rag_token
        self._client = client or httpx.Client(
            base_url=self._base,
            timeout=httpx.Timeout(settings.legal_rag_timeout),
            headers=headers,
        )

    # ------------------------------------------------------------ 内部

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise DomainError(
                "法律检索服务不可达（{0}）：{1}".format(self._base, exc),
                code="retrieval.unreachable",
            ) from exc
        return self._unwrap(resp)

    def _get(self, path: str) -> Dict[str, Any]:
        try:
            resp = self._client.get(path)
        except httpx.HTTPError as exc:
            raise DomainError(
                "法律检索服务不可达（{0}）：{1}".format(self._base, exc),
                code="retrieval.unreachable",
            ) from exc
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Dict[str, Any]:
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            raise DomainError(
                "法律检索服务返回 {0}：{1}".format(resp.status_code, detail),
                code="retrieval.upstream_error",
            )
        return resp.json()

    # ------------------------------------------------------------ RetrievalPort

    def health(self) -> Dict[str, Any]:
        return self._get("/health")

    def lookup(self, source_ids):
        """按 source_id 反查。检索服务不可用时**返回空**而不是抛异常 ——
        引用体检是锦上添花，不该因为它挂掉就打不开文书。"""
        ids = [x for x in (source_ids or []) if x]
        if not ids:
            return {"items": {}, "corpus_version": ""}
        try:
            return self._post("/lookup", {"source_ids": ids[:200]})
        except Exception as exc:                         # noqa: BLE001
            logger.warning("引用反查失败：%s", exc)
            return {"items": {}, "corpus_version": "", "error": str(exc)}

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        data = self._post("/search", {
            "query": request.query,
            "case_context": _ctx(request.case_context),
            "scope": list(request.scope),
            "top_k": request.top_k,
        })
        return RetrievalResult(
            items=[_item(x) for x in data.get("items", [])],
            citations=[_citation(x) for x in data.get("citations", [])],
            answer=data.get("answer", ""),
            trace_id=data.get("trace_id", ""),
            corpus_version=data.get("corpus_version", ""),
            cost=data.get("cost") or {},
        )

    # ------------------------------------------------------------ LegalQAPort

    def _submit(self, kind: str, payload: Dict[str, Any]) -> QAJob:
        payload["kind"] = kind
        data = self._post("/jobs", payload)
        return QAJob(job_id=data["job_id"], kind=kind, status=data.get("status", "queued"))

    def submit_answer(
        self,
        question: str,
        case_context: Optional[CaseContext] = None,
        history: Optional[List[Any]] = None,
        top_k: int = 8,
    ) -> QAJob:
        return self._submit("answer", {
            "query": question,
            "case_context": _ctx(case_context),
            "history": history or [],
            "top_k": top_k,
        })

    def submit_extract(
        self, text: str, case_context: Optional[CaseContext] = None
    ) -> QAJob:
        return self._submit("extract", {
            "text": text, "case_context": _ctx(case_context),
        })

    def submit_compare(
        self, question: str, case_context: Optional[CaseContext] = None
    ) -> QAJob:
        return self._submit("compare", {
            "query": question, "case_context": _ctx(case_context),
        })

    def poll(self, job_id: str) -> QAJob:
        data = self._get("/jobs/{0}".format(job_id))
        return QAJob(
            job_id=job_id,
            kind=data.get("kind", ""),
            status=data.get("status", ""),
            error=data.get("error", ""),
            elapsed=float(data.get("elapsed") or 0.0),
        )

    def result(self, job_id: str) -> Any:
        data = self._get("/jobs/{0}".format(job_id))
        status = data.get("status")
        if status == "failed":
            raise DomainError(
                "检索任务失败：{0}".format(data.get("error", "")),
                code="retrieval.job_failed",
            )
        if status != "done":
            raise DomainError(
                "任务尚未完成（当前 {0}）".format(status), code="retrieval.job_pending"
            )
        return self._to_result(data.get("result") or {})

    def drop(self, job_id: str) -> None:
        try:
            self._client.delete("/jobs/{0}".format(job_id))
        except httpx.HTTPError:
            pass       # 清理失败不影响主流程

    @staticmethod
    def _to_result(raw: Dict[str, Any]) -> Any:
        kind = raw.get("kind", "answer")
        citations = [_citation(x) for x in raw.get("citations", [])]
        cost = raw.get("cost") or {}
        trace = raw.get("trace_id", "")

        if kind == "extract":
            el = raw.get("elements") or {}
            return Elements(
                cause_of_action=el.get("案由", ""),
                legal_relation=el.get("法律关系", ""),
                parties=list(el.get("当事人") or []),
                key_facts=list(el.get("关键事实") or []),
                disputes=list(el.get("争议焦点") or []),
                amount=el.get("涉案金额", ""),
                timeline=list(el.get("时间线") or []),
                missing=list(el.get("缺失信息") or []),
                trace_id=trace, cost=cost,
            )

        if kind == "compare":
            cmp_ = raw.get("comparison") or {}
            return CaseComparison(
                rows=list(cmp_.get("rows") or []),
                conclusion=cmp_.get("结论", ""),
                citations=citations, trace_id=trace, cost=cost,
            )

        return Answer(
            text=raw.get("answer", ""),
            citations=citations,
            items=[_item(x) for x in raw.get("items", [])],
            used=list(raw.get("used") or []),
            warnings=list(raw.get("warnings") or []),
            queries=list(raw.get("queries") or []),
            intent=raw.get("intent", ""),
            trace_id=trace,
            corpus_version=raw.get("corpus_version", ""),
            cost=cost,
        )


def build_retrieval_adapter(settings: Settings, client: Any = None) -> Any:
    """给 bootstrap 用的工厂。backend=fake 时返回假实现，检索服务没起也能开发。"""
    if settings.retrieval_backend == "fake":
        from adapters.retrieval.fake import FakeLegalRag

        return FakeLegalRag()
    return LegalRagHttp(settings, client=client)
