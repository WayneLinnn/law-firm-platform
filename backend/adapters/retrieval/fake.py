"""假检索实现 —— 检索服务没起时的本地开发与 CI 用。

设 LFP_RETRIEVAL_BACKEND=fake 即可启用。结果固定，不联网、不花钱，
让业务模块的测试不依赖外部进程。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from kernel.ports.legal_qa import Answer, CaseComparison, Elements, QAJob
from kernel.ports.retrieval import (
    CaseContext,
    Citation,
    RetrievalItem,
    RetrievalRequest,
    RetrievalResult,
)

FAKE_CITATIONS = [
    Citation(
        source_type="law",
        source_id="法释〔2020〕25号#第十四条",
        title="建工解释一 第十四条",
        snippet="建设工程未经竣工验收，发包人擅自使用后，又以使用部分质量不符合约定为由主张权利的，人民法院不予支持…",
        effective_status="现行有效（2021-01-01 施行）",
        locator="4321",
    ),
    Citation(
        source_type="case",
        source_id="指导案例7号",
        title="牡丹江宏阁诉华隆建设工程施工合同纠纷案",
        snippet="…实际施工人可以突破合同相对性向发包人主张工程款…",
        effective_status="现行有效",
        locator="1234",
    ),
]


class FakeLegalRag:
    """实现 RetrievalPort 与 LegalQAPort。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._jobs: Dict[str, Dict[str, Any]] = {}

    # ---------------- RetrievalPort

    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "corpus_version": "fake-v1",
                "model": "fake", "embedding": "fake"}

    def lookup(self, source_ids) -> Dict[str, Any]:
        """假的反查。默认**一律返回"没变"** —— 引用体检在假实现下不该报警。

        要造"法条变了"的场景，测试自己塞 `self.lookup_result`
        （见 test_citation.py 里的桩）。
        """
        canned = getattr(self, "lookup_result", None)
        if canned is not None:
            return canned
        return {"items": {sid: {"found": True, "snippet": "", "title": "",
                                "effective_status": ""}
                          for sid in (source_ids or [])},
                "corpus_version": "fake-v1"}

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        self.calls.append({"op": "search", "query": request.query,
                           "context": request.case_context})
        items = [
            RetrievalItem(title=c.title, summary=c.snippet[:60], score=1.0 - i * 0.1,
                          citation=c, meta={"index": i + 1, "exact": i == 0})
            for i, c in enumerate(FAKE_CITATIONS[: request.top_k])
        ]
        return RetrievalResult(
            items=items, citations=[i.citation for i in items],
            trace_id=uuid.uuid4().hex, corpus_version="fake-v1",
            cost={"cny": 0.0, "calls": 0},
        )

    # ---------------- LegalQAPort

    def _new_job(self, kind: str, payload: Dict[str, Any]) -> QAJob:
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"kind": kind, "payload": payload}
        self.calls.append({"op": kind, **payload})
        return QAJob(job_id=job_id, kind=kind, status="queued")

    def submit_answer(self, question, case_context=None, history=None, top_k=8) -> QAJob:
        return self._new_job("answer", {"question": question,
                                        "context": case_context, "top_k": top_k})

    def submit_extract(self, text, case_context=None) -> QAJob:
        return self._new_job("extract", {"text": text, "context": case_context})

    def submit_compare(self, question, case_context=None) -> QAJob:
        return self._new_job("compare", {"question": question, "context": case_context})

    def poll(self, job_id: str) -> QAJob:
        job = self._jobs.get(job_id)
        if job is None:
            return QAJob(job_id=job_id, kind="", status="failed", error="任务不存在")
        return QAJob(job_id=job_id, kind=job["kind"], status="done")

    def result(self, job_id: str) -> Any:
        job = self._jobs[job_id]
        cost = {"cny": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0}
        if job["kind"] == "extract":
            return Elements(cause_of_action="建设工程施工合同纠纷",
                            disputes=["未经竣工验收是否影响工程款主张"],
                            trace_id=job_id, cost=cost)
        if job["kind"] == "compare":
            return CaseComparison(rows=[{"案例": "指导案例7号", "裁判要旨": "…"}],
                                  conclusion="可参照", trace_id=job_id, cost=cost)
        return Answer(
            text="（假实现）工程未经竣工验收但发包人已实际使用的，可以主张工程款。【S1】",
            citations=list(FAKE_CITATIONS), used=[1],
            queries=["假改写1", "假改写2"], intent="case_search",
            trace_id=job_id, corpus_version="fake-v1", cost=cost,
        )

    def drop(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
