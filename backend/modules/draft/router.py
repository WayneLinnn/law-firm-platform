from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.draft import service, templates as tpl

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class SectionOut(BaseModel):
    id: int
    ordinal: int
    key: str
    title: str
    #: fill 自动填充 / generate 模型起草 / manual 律师手写
    kind: str
    kind_label: str
    content: str
    #: 这段实际怎么来的：fill / model / manual / empty
    source: str
    edited: bool
    used: List[int]
    warnings: List[str]
    note: str
    hint: str
    required: bool
    updated_by: Optional[int]
    updated_at: datetime


class DraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    template_key: str
    title: str
    status: str
    source_run_id: str
    document_id: Optional[int]
    created_by: int
    finalized_by: Optional[int]
    finalized_at: Optional[datetime]
    cost_cny: str
    created_at: datetime
    updated_at: datetime


class DraftDetailOut(DraftOut):
    sections: List[SectionOut]
    citations: List[Dict[str, Any]]
    ready: bool
    blocking: List[str]
    warnings: List[str]


class CreateIn(BaseModel):
    case_id: int
    template_key: str
    title: str = ""
    #: 挂到某条流程上，直接复用它检索到的依据
    run_id: str = ""
    #: 没有流程时，用这些检索式自己取依据
    queries: List[str] = Field(default_factory=list)


class GenerateIn(BaseModel):
    instruction: str = ""
    #: 覆盖律师手工改过的内容 —— 要显式确认
    force: bool = False


class EditIn(BaseModel):
    content: str


class FinalizeIn(BaseModel):
    note: str = ""


# ---------------------------------------------------------------- 接口


@router.get("/templates")
def list_templates(
    principal: Principal = Depends(require_perm("draft.view")),
) -> List[Dict[str, Any]]:
    return tpl.describe()


@router.post("/drafts", response_model=DraftDetailOut)
def create_draft(
    payload: CreateIn,
    request: Request,
    principal: Principal = Depends(require_perm("draft.create")),
    session: Session = Depends(get_session),
) -> DraftDetailOut:
    row = service.create(session, principal, payload.case_id, payload.template_key,
                         payload.title, payload.run_id, payload.queries)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="draft.create", resource_type="case",
                resource_id=str(payload.case_id), ip=client_ip(request),
                detail={"draft_id": row.id, "template": payload.template_key})
    return _detail(session, principal, row)


@router.get("/cases/{case_id}/drafts", response_model=List[DraftOut])
def case_drafts(
    case_id: int,
    principal: Principal = Depends(require_perm("draft.view")),
    session: Session = Depends(get_session),
) -> List[DraftOut]:
    return [DraftOut.model_validate(d)
            for d in service.drafts_of_case(session, principal, case_id)]


@router.get("/drafts/{draft_id}", response_model=DraftDetailOut)
def get_draft(
    draft_id: int,
    principal: Principal = Depends(require_perm("draft.view")),
    session: Session = Depends(get_session),
) -> DraftDetailOut:
    return _detail(session, principal, service.get_draft(session, principal, draft_id))


@router.post("/drafts/{draft_id}/sections/{key}/generate", response_model=DraftDetailOut)
def generate(
    draft_id: int,
    key: str,
    payload: GenerateIn,
    principal: Principal = Depends(require_perm("draft.create")),
    session: Session = Depends(get_session),
) -> DraftDetailOut:
    """生成一节。律师改过的内容默认不覆盖 —— 要覆盖传 force。"""
    service.generate_section(session, principal, draft_id, key,
                             payload.instruction, payload.force)
    return _detail(session, principal, service.get_draft(session, principal, draft_id))


@router.put("/drafts/{draft_id}/sections/{key}", response_model=DraftDetailOut)
def edit(
    draft_id: int,
    key: str,
    payload: EditIn,
    principal: Principal = Depends(require_perm("draft.create")),
    session: Session = Depends(get_session),
) -> DraftDetailOut:
    service.edit_section(session, principal, draft_id, key, payload.content)
    return _detail(session, principal, service.get_draft(session, principal, draft_id))


@router.get("/drafts/{draft_id}/text")
def full_text(
    draft_id: int,
    principal: Principal = Depends(require_perm("draft.view")),
    session: Session = Depends(get_session),
) -> Dict[str, str]:
    return {"text": service.assemble(session, principal, draft_id)}


@router.post("/drafts/{draft_id}/finalize", response_model=DraftDetailOut)
def finalize(
    draft_id: int,
    payload: FinalizeIn,
    request: Request,
    principal: Principal = Depends(require_perm("draft.finalize")),
    session: Session = Depends(get_session),
) -> DraftDetailOut:
    """定稿并归档进案卷。**引了不存在的编号一律不放行。**"""
    row = service.finalize(session, principal, draft_id, payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="draft.finalize", resource_type="case",
                resource_id=str(row.case_id), ip=client_ip(request),
                detail={"draft_id": row.id, "document_id": row.document_id,
                        "note": payload.note})
    return _detail(session, principal, row)


@router.post("/drafts/{draft_id}/void", response_model=DraftOut)
def void(
    draft_id: int,
    principal: Principal = Depends(require_perm("draft.create")),
    session: Session = Depends(get_session),
) -> DraftOut:
    return DraftOut.model_validate(service.void(session, principal, draft_id))


# ---------------------------------------------------------------- 内部


def _detail(session: Session, principal: Principal, row) -> DraftDetailOut:
    t = tpl.get(row.template_key)
    specs = {s.key: s for s in (t.sections if t else [])}
    state = service.readiness(session, principal, row.id)

    out = []
    for s in service.sections_of(session, row.id):
        spec = specs.get(s.key)
        out.append(SectionOut(
            id=s.id, ordinal=s.ordinal, key=s.key, title=s.title, kind=s.kind,
            kind_label=tpl.KIND_LABELS.get(s.kind, s.kind),
            content=s.content, source=s.source, edited=s.edited,
            used=json.loads(s.used or "[]"),
            warnings=json.loads(s.warnings or "[]"),
            note=s.note,
            hint=(spec.hint if spec else ""),
            required=(spec.required if spec else True),
            updated_by=s.updated_by, updated_at=s.updated_at,
        ))

    return DraftDetailOut(
        **DraftOut.model_validate(row).model_dump(),
        sections=out,
        citations=json.loads(row.citations or "[]"),
        ready=state["ready"], blocking=state["blocking"], warnings=state["warnings"],
    )


DraftDetailOut.model_rebuild()
