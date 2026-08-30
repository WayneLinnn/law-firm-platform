"""检索的业务逻辑。

模块本身很薄 —— 真正的检索在独立服务里。这里只做三件事：

  1. **注入案件上下文** —— 这是平台相对于「一个搜索框」的全部价值。
     案由、案情、争议焦点、对方主张会被检索服务喂进改写节点，
     围绕它们生成检索式。实测：同一个问题带上"已完工、已实际使用、
     未验收"，改写出的检索式直接命中建工解释一第十四条；不带就是
     一堆泛泛的工程款条文。

  2. **留档** —— trace_id 是 Step 13 引用留痕的锚点；花费按租户记账。

  3. **长任务的状态同步** —— 检索服务重启会丢任务，这边要能识别。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.container import container
from kernel.errors import DomainError, NotFound
from kernel.ports.legal_qa import Answer, CaseComparison, Elements
from kernel.ports.retrieval import CaseContext, RetrievalRequest
from kernel.registry import registry
from modules.retrieval.models import QUERY_KINDS, SearchCitation, SearchLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 案件上下文


def build_case_context(session: Session, case_id: Optional[int]) -> Optional[CaseContext]:
    """从案件里装配检索上下文。

    ★ 只取**案件本身的事实**，不取当事人姓名以外的敏感信息 ——
      案情摘要会发给模型，能少发就少发。
    """
    if case_id is None:
        return None
    case_api = registry.optional_api("case")
    if case_api is None:
        return None

    case = case_api.get_case(session, case_id)
    cause_name = ""
    if case.cause_id:
        cause_name = case_api.get_cause(session, case.cause_id).name

    opponent = ""
    crm = registry.optional_api("crm")
    if crm is not None:
        names = []
        for party in case_api.parties_of(session, case_id):
            if not party.is_ours:
                names.append(crm.get_party(session, party.party_id).name)
        if names:
            opponent = "对方当事人：" + "、".join(names)

    return CaseContext(
        case_id=str(case_id),
        cause_of_action=cause_name,
        facts=case.summary or case.title,
        # 争议焦点暂时没有独立字段，Step 10 的流程会补上
        disputes=[],
        opponent_claims=opponent,
    )


# ---------------------------------------------------------------- 留档


def _log(
    session: Session,
    tenant_id: int,
    actor_id: int,
    case_id: Optional[int],
    kind: str,
    question: str,
    status: str = "done",
) -> SearchLog:
    if kind not in QUERY_KINDS:
        raise DomainError("未知的检索类型 {0}".format(kind), code="retrieval.bad_kind")
    log = SearchLog(
        tenant_id=tenant_id, actor_id=actor_id, case_id=case_id,
        kind=kind, status=status, question=question.strip(),
    )
    session.add(log)
    session.flush()
    return log


def _save_citations(
    session: Session, log: SearchLog, citations, used: Optional[List[int]] = None
) -> None:
    used_set = set(used or [])
    for i, c in enumerate(citations, 1):
        session.add(SearchCitation(
            tenant_id=log.tenant_id, log_id=log.id, ordinal=i,
            source_type=c.source_type, source_id=c.source_id, title=c.title,
            snippet=c.snippet, url=c.url, locator=c.locator,
            effective_status=c.effective_status,
            used=i in used_set,
        ))
    session.flush()


def _finish(session: Session, log: SearchLog, cost: Dict[str, Any]) -> None:
    log.status = "done"
    log.finished_at = datetime.now(timezone.utc)
    log.tokens_in = int(cost.get("tokens_in") or 0)
    log.tokens_out = int(cost.get("tokens_out") or 0)
    log.cost_cny = Decimal(str(cost.get("cny") or 0))
    session.flush()


# ---------------------------------------------------------------- 纯检索


def search(
    session: Session,
    tenant_id: int,
    actor_id: int,
    query: str,
    case_id: Optional[int] = None,
    scope: Optional[List[str]] = None,
    top_k: int = 10,
) -> Dict[str, Any]:
    """纯检索：0 成本、秒级、结果确定。

    ★ **纯检索不支持「带本案背景」。**
      案件上下文是喂给改写节点的，而 /search 直接查 BM25+向量，不走改写 ——
      传了也不会影响结果。实测 A/B：带与不带，返回的 5 条引用一模一样。

      所以这里根本不接受 with_context 参数，免得界面上出现一个骗人的开关。
      要让案情真正影响检索，用 submit()（走完整图）。
    """
    if not query.strip():
        raise DomainError("检索内容不能为空", code="retrieval.empty_query")

    port = container.get("retrieval")
    # 只用于留档：记下这次检索发生在哪个案件下
    context = build_case_context(session, case_id)
    log = _log(session, tenant_id, actor_id, case_id, "search", query)

    result = port.search(RetrievalRequest(
        query=query, case_context=context,
        scope=scope or ["law", "case"], top_k=top_k,
    ))

    log.trace_id = result.trace_id
    log.corpus_version = result.corpus_version
    _save_citations(session, log, result.citations)
    _finish(session, log, result.cost or {})
    # EVENT: retrieval.searched
    return {"log": log, "result": result}


# ---------------------------------------------------------------- 长任务


def submit(
    session: Session,
    tenant_id: int,
    actor_id: int,
    kind: str,
    question: str,
    case_id: Optional[int] = None,
    history: Optional[List[Any]] = None,
    top_k: int = 8,
    with_context: bool = True,
) -> SearchLog:
    """提交长任务。实测问答 15-26 秒、要素抽取 43 秒，所以不做同步等待。"""
    if kind not in ("answer", "extract", "compare"):
        raise DomainError("长任务只支持 answer / extract / compare",
                          code="retrieval.bad_kind")
    if not question.strip():
        raise DomainError("检索内容不能为空", code="retrieval.empty_query")

    port = container.get("legal_qa")
    context = build_case_context(session, case_id) if with_context else None
    log = _log(session, tenant_id, actor_id, case_id, kind, question, status="queued")

    if kind == "extract":
        job = port.submit_extract(question, case_context=context)
    elif kind == "compare":
        job = port.submit_compare(question, case_context=context)
    else:
        job = port.submit_answer(question, case_context=context,
                                 history=history or [], top_k=top_k)

    log.job_id = job.job_id
    log.status = job.status
    session.flush()
    # EVENT: retrieval.submitted
    return log


def poll(session: Session, log_id: int) -> SearchLog:
    """轮询任务。完成时把结果落库。

    ★ 检索服务重启会丢任务（任务表在进程内存里）。这时轮询会拿到
      "任务不存在"，要把本地记录标记为失败，而不是一直转圈。
    """
    log = get_log(session, log_id)
    if log.status in ("done", "failed"):
        return log

    port = container.get("legal_qa")
    job = port.poll(log.job_id)

    if job.status == "failed":
        log.status = "failed"
        log.error = job.error or "检索服务未返回原因（服务可能已重启）"
        log.finished_at = datetime.now(timezone.utc)
        session.flush()
        return log

    if job.status != "done":
        log.status = job.status
        session.flush()
        return log

    result = port.result(log.job_id)
    _apply_result(session, log, result)
    port.drop(log.job_id)
    return log


def _apply_result(session: Session, log: SearchLog, result: Any) -> None:
    if isinstance(result, Answer):
        log.answer = result.text
        log.intent = result.intent
        log.queries = json.dumps(result.queries, ensure_ascii=False)
        log.warnings = json.dumps(result.warnings, ensure_ascii=False)
        log.trace_id = result.trace_id
        log.corpus_version = result.corpus_version
        _save_citations(session, log, result.citations, used=result.used)
        _finish(session, log, result.cost or {})
        return

    if isinstance(result, Elements):
        log.answer = json.dumps({
            "案由": result.cause_of_action, "法律关系": result.legal_relation,
            "当事人": result.parties, "关键事实": result.key_facts,
            "争议焦点": result.disputes, "涉案金额": result.amount,
            "时间线": result.timeline, "缺失信息": result.missing,
        }, ensure_ascii=False)
        log.trace_id = result.trace_id
        _finish(session, log, result.cost or {})
        return

    if isinstance(result, CaseComparison):
        log.answer = json.dumps({"rows": result.rows, "结论": result.conclusion},
                                ensure_ascii=False)
        log.trace_id = result.trace_id
        _save_citations(session, log, result.citations)
        _finish(session, log, result.cost or {})
        return

    raise DomainError("未知的检索结果类型", code="retrieval.bad_result")


# ---------------------------------------------------------------- 查询


def get_log(session: Session, log_id: int) -> SearchLog:
    log = session.get(SearchLog, log_id)
    if log is None:
        raise NotFound("检索记录 {0} 不存在".format(log_id))
    return log


def citations_of(session: Session, log_id: int) -> List[SearchCitation]:
    return list(session.scalars(
        select(SearchCitation)
        .where(SearchCitation.log_id == log_id)
        .order_by(SearchCitation.ordinal)
    ))


def logs_of_case(session: Session, case_id: int, limit: int = 50) -> List[SearchLog]:
    """某案件下的检索历史 —— 律师要能看"我上次查过什么"。"""
    return list(session.scalars(
        select(SearchLog)
        .where(SearchLog.case_id == case_id)
        .order_by(SearchLog.id.desc())
        .limit(limit)
    ))


def cost_of_tenant(session: Session, tenant_id: int) -> Dict[str, Any]:
    """租户累计花费。**先只记账不限额** —— 等真有滥用再加配额。"""
    from sqlalchemy import func as sqlfunc

    row = session.execute(
        select(
            sqlfunc.count(SearchLog.id),
            sqlfunc.coalesce(sqlfunc.sum(SearchLog.cost_cny), 0),
            sqlfunc.coalesce(sqlfunc.sum(SearchLog.tokens_in), 0),
            sqlfunc.coalesce(sqlfunc.sum(SearchLog.tokens_out), 0),
        ).where(SearchLog.tenant_id == tenant_id)
    ).one()
    return {"count": row[0], "cost_cny": float(row[1]),
            "tokens_in": int(row[2]), "tokens_out": int(row[3])}


def health(session: Session) -> Dict[str, Any]:
    """检索服务是否可用。界面上要能看出"是服务没起还是真没结果"。"""
    try:
        return container.get("retrieval").health()
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)[:200]}


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的检索记录 —— 「我们查过什么」也是办案过程的一部分。"""
    registry.api("case").assert_visible(session, principal, case_id)
    rows = logs_of_case(session, case_id, limit=1000)
    if not rows:
        return []
    return [{"name": "法律检索记录", "count": len(rows), "note": ""}]
