from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.retrieval import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class CitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ordinal: int
    source_type: str
    #: ★ 稳定标识（法释文号#条号 / 指导案例号 / 案号），不是 chunk_id
    source_id: str
    title: str
    snippet: str
    url: str
    effective_status: str
    used: bool


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: Optional[int]
    actor_id: int
    kind: str
    status: str
    question: str
    intent: str
    answer: str
    trace_id: str
    corpus_version: str
    tokens_in: int
    tokens_out: int
    error: str
    created_at: datetime
    finished_at: Optional[datetime]


class LogDetailOut(LogOut):
    citations: List[CitationOut]
    queries: List[str]
    warnings: List[str]
    cost_cny: float


class SearchIn(BaseModel):
    query: str
    case_id: Optional[int] = None
    scope: List[str] = Field(default_factory=lambda: ["law", "case"])
    top_k: int = Field(default=10, ge=1, le=50)
    # 注意：纯检索**没有**「带本案背景」开关 —— 它不走改写节点，
    # 案件上下文传了也不影响结果。要让案情生效请用 /ask。


class AskIn(BaseModel):
    kind: str = Field(default="answer", description="answer | extract | compare")
    question: str
    case_id: Optional[int] = None
    history: List[Any] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=30)
    with_context: bool = True


# ---------------------------------------------------------------- 内部


def _detail(session: Session, log) -> LogDetailOut:
    return LogDetailOut(
        **LogOut.model_validate(log).model_dump(),
        citations=[CitationOut.model_validate(c)
                   for c in service.citations_of(session, log.id)],
        queries=json.loads(log.queries or "[]"),
        warnings=json.loads(log.warnings or "[]"),
        cost_cny=float(log.cost_cny or 0),
    )


def _assert_case_visible(session: Session, principal: Principal,
                         case_id: Optional[int]) -> None:
    """挂在案件下的检索，要过案件的数据范围与隔离墙。"""
    if case_id is None:
        return
    case_api = registry.optional_api("case")
    if case_api is not None:
        case_api.assert_visible(session, principal, case_id)


# ---------------------------------------------------------------- 接口


@router.get("/health")
def health(
    _: Principal = Depends(require_perm("retrieval.search")),
    session: Session = Depends(get_session),
) -> dict:
    """检索服务是否可用。界面要能区分"服务没起"和"真没结果"。"""
    return service.health(session)


@router.post("/search", response_model=LogDetailOut)
def do_search(
    payload: SearchIn,
    request: Request,
    principal: Principal = Depends(require_perm("retrieval.search")),
    session: Session = Depends(get_session),
) -> LogDetailOut:
    """纯检索：0 成本、秒级、结果确定。**案件上下文在这条路上不生效。**"""
    _assert_case_visible(session, principal, payload.case_id)
    out = service.search(
        session, principal.tenant_id, principal.user_id, payload.query,
        case_id=payload.case_id, scope=payload.scope, top_k=payload.top_k,
    )
    log = out["log"]
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="retrieval.search", resource_type="case",
                resource_id=str(payload.case_id or ""), ip=client_ip(request),
                detail={"log_id": log.id, "query": payload.query[:120],
                        "corpus": log.corpus_version})
    return _detail(session, log)


@router.post("/ask", response_model=LogOut)
def ask(
    payload: AskIn,
    request: Request,
    principal: Principal = Depends(require_perm("retrieval.ask")),
    session: Session = Depends(get_session),
) -> LogOut:
    """提交长任务。**不同步等待** —— 问答 15-26 秒、要素抽取 43 秒。

    返回 log_id，前端轮询 /logs/{id}/poll。
    """
    _assert_case_visible(session, principal, payload.case_id)
    log = service.submit(
        session, principal.tenant_id, principal.user_id, payload.kind,
        payload.question, case_id=payload.case_id, history=payload.history,
        top_k=payload.top_k, with_context=payload.with_context,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="retrieval.ask", resource_type="case",
                resource_id=str(payload.case_id or ""), ip=client_ip(request),
                detail={"log_id": log.id, "kind": payload.kind,
                        "question": payload.question[:120],
                        "with_context": payload.with_context})
    return LogOut.model_validate(log)


@router.post("/logs/{log_id}/poll", response_model=LogDetailOut)
def poll(
    log_id: int,
    principal: Principal = Depends(require_perm("retrieval.ask")),
    session: Session = Depends(get_session),
) -> LogDetailOut:
    log = service.get_log(session, log_id)
    _assert_case_visible(session, principal, log.case_id)
    return _detail(session, service.poll(session, log_id))


@router.get("/logs/{log_id}", response_model=LogDetailOut)
def get_log(
    log_id: int,
    principal: Principal = Depends(require_perm("retrieval.view")),
    session: Session = Depends(get_session),
) -> LogDetailOut:
    log = service.get_log(session, log_id)
    _assert_case_visible(session, principal, log.case_id)
    return _detail(session, log)


@router.get("/cases/{case_id}/logs", response_model=List[LogOut])
def case_logs(
    case_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_perm("retrieval.view")),
    session: Session = Depends(get_session),
) -> List[LogOut]:
    """某案件下的检索历史 —— 律师要能看"我上次查过什么"。"""
    _assert_case_visible(session, principal, case_id)
    return [LogOut.model_validate(x)
            for x in service.logs_of_case(session, case_id, limit)]


@router.get("/cost")
def cost(
    principal: Principal = Depends(require_perm("retrieval.cost")),
    session: Session = Depends(get_session),
) -> dict:
    """租户累计花费。**先只记账不限额。**"""
    return service.cost_of_tenant(session, principal.tenant_id)


class LookupIn(BaseModel):
    source_ids: List[str] = Field(description="引用标识，如 民法典_合同编#第八百零一条")


@router.post("/lookup")
def lookup(
    payload: LookupIn,
    principal: Principal = Depends(require_perm("retrieval.view")),
    session: Session = Depends(get_session),
) -> dict:
    """按引用标识反查原文 —— **让每一条依据都点得开**。

    ★ 原来只有引用体检在用这条路（走的是 Port，没透到 HTTP）。
      于是办案流程里那些依据成了纯文本：屏幕上写着
      「民法典_合同编#第八百零一条」，律师却点不动它。

      **看到什么就该能点什么** —— 一条打不开的依据，
      和没有依据的区别只在于它让人以为查过了。

    ★ 只读、0 成本。查不到返回 `found: False`，**不报错** ——
      库重建、法条下架都会让老引用查不到，那是正常情况。
    """
    if not payload.source_ids:
        return {"items": {}, "corpus_version": ""}
    port = registry.port("retrieval")
    return port.lookup(payload.source_ids[:20])
