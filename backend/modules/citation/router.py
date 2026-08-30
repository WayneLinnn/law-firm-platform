from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.citation import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm


def _parts(session: Session, principal: Principal, doc_kind: str, doc_id: int):
    """取文书各节。**权限在产出方那边判** —— 引用模块不认识文书是什么。"""
    if doc_kind == "draft":
        draft = registry.optional_api("draft")
        if draft is None:
            return []
        draft.get_draft(session, principal, doc_id)      # 过一遍案件可见性
        return draft.parts_of(session, doc_id)
    return []


@router.get("/{doc_kind}/{doc_id}/map")
def sentence_map(
    doc_kind: str,
    doc_id: int,
    principal: Principal = Depends(require_perm("citation.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """每一节的每一句，带上它引了哪些依据。

    ★ 没有依据的句子也返回并标出来 —— 律师签字前要找的正是那些没出处的论断。
    """
    return service.sentence_map(session, doc_kind, doc_id,
                                _parts(session, principal, doc_kind, doc_id))


@router.get("/{doc_kind}/{doc_id}/sources")
def sources(
    doc_kind: str,
    doc_id: int,
    principal: Principal = Depends(require_perm("citation.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    _parts(session, principal, doc_kind, doc_id)         # 过权限
    return [{
        "id": c.id, "ordinal": c.ordinal,
        "source_type": c.source_type, "source_id": c.source_id,
        "title": c.title, "snippet": c.snippet,
        "effective_status": c.effective_status,
        "corpus_version": c.corpus_version, "trace_id": c.trace_id,
        "issue": c.issue,
        "retrieved_at": c.retrieved_at.isoformat() if c.retrieved_at else "",
    } for c in service.sources_of(session, doc_kind, doc_id)]


@router.get("/{doc_kind}/{doc_id}/coverage")
def coverage(
    doc_kind: str,
    doc_id: int,
    principal: Principal = Depends(require_perm("citation.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.coverage(session, doc_kind, doc_id,
                            _parts(session, principal, doc_kind, doc_id))


@router.get("/{doc_kind}/{doc_id}/unsupported")
def unsupported(
    doc_kind: str,
    doc_id: int,
    principal: Principal = Depends(require_perm("citation.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """看着像法律论断、但没标依据的句子。

    ★ 这是**提示**不是**判定**。说成判定，律师发现几次误报就再也不看了 ——
      那时真正没依据的论断也会被一起忽略。
    """
    return service.unsupported(session, doc_kind, doc_id,
                               _parts(session, principal, doc_kind, doc_id))


@router.get("/citations/{citation_id}")
def trace(
    citation_id: int,
    principal: Principal = Depends(require_perm("citation.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """一条依据的完整来路 —— 律师问「你这条哪来的」时的答案。"""
    return service.trace(session, principal, citation_id)


@router.get("/{doc_kind}/{doc_id}/recheck")
def recheck(
    doc_kind: str,
    doc_id: int,
    principal: Principal = Depends(require_perm("citation.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """★ 引用体检：这份文书引的东西，现在还是当时那样吗？

    法条会修订、会废止，而文书里的引用存的是**当时的快照**。
    这个接口把每条引用拿回库里比一遍，报告哪些变了。

    ★ **只报告不改** —— 自动替换成新法条更危险，
      律师会以为还是他核过的那句。
    """
    return service.recheck(session, principal, doc_kind, doc_id)
