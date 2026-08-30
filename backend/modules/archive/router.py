from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.archive import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


class ArchiveIn(BaseModel):
    note: str = ""
    keep_years: int = Field(default=10, ge=0, le=100)
    #: 带阻断项强制归档。需要 archive.force，且阻断项会记进 note
    force: bool = False


class UnarchiveIn(BaseModel):
    reason: str = Field(description="解档理由 —— 必填")
    to_status: str = "closed"


@router.get("/cases/{case_id}/inventory")
def inventory(
    case_id: int,
    principal: Principal = Depends(require_perm("archive.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """卷宗目录：本卷共几件、都是什么。**不改任何东西**，随时能看。"""
    return service.inventory(session, principal, case_id)


@router.get("/cases/{case_id}/blockers")
def blockers(
    case_id: int,
    principal: Principal = Depends(require_perm("archive.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, str]]:
    """归档前还有什么没了结。★ 钱和活都清了才该归档。"""
    return service.blockers(session, principal, case_id)


@router.get("/cases/{case_id}/record")
def record(
    case_id: int,
    principal: Principal = Depends(require_perm("archive.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    r = service.current(session, principal, case_id)
    return service.describe(r) if r else {}


@router.get("/cases/{case_id}/history")
def history(
    case_id: int,
    principal: Principal = Depends(require_perm("archive.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """归档/解档全历史 —— 解过几次档，各是为什么。"""
    return [service.describe(r)
            for r in service.history(session, principal, case_id)]


@router.post("/cases/{case_id}/archive")
def do_archive(
    case_id: int,
    payload: ArchiveIn,
    principal: Principal = Depends(require_perm("archive.do")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """归档：编目录 → 存指纹 → 案件转为已归档，之后**只读**。"""
    r = service.archive(session, principal, case_id, payload.note,
                        payload.keep_years, payload.force)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="archive.archived", resource_type="case",
                resource_id=str(case_id),
                detail={"volume_no": r.volume_no, "items": r.item_count,
                        "fingerprint": r.fingerprint, "force": payload.force})
    return service.describe(r)


@router.post("/cases/{case_id}/unarchive")
def do_unarchive(
    case_id: int,
    payload: UnarchiveIn,
    principal: Principal = Depends(require_perm("archive.unarchive")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """解档。★ 案子会再审、会申请执行 —— 必须能解开，但要写理由并留痕。"""
    r = service.unarchive(session, principal, case_id, payload.reason,
                          payload.to_status)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="archive.unarchived", resource_type="case",
                resource_id=str(case_id),
                detail={"volume_no": r.volume_no, "reason": payload.reason})
    return service.describe(r)


@router.get("/cases/{case_id}/verify")
def verify(
    case_id: int,
    principal: Principal = Depends(require_perm("archive.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """★ 这卷被动过吗？重算指纹和归档时比。正常永远相同。"""
    return service.verify(session, principal, case_id)


@router.get("/cases/{case_id}/tab-counts")
def tab_counts(
    case_id: int,
    principal: Principal = Depends(require_perm("archive.view")),
    session: Session = Depends(get_session),
) -> Dict[str, int]:
    """案件页每个页签上该显示几。

    ★ 不点开就知道哪里有东西 —— Clio 的页签也是这么做的。
    """
    return service.tab_counts(session, principal, case_id)
