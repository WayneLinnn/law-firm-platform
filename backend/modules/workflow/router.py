from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.workflow import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node: str
    event: str
    note: str
    actor_id: Optional[int]
    created_at: datetime


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    case_id: int
    template_key: str
    status: str
    current_node: str
    pending_question: str
    error: str
    started_by: int
    started_at: datetime
    finished_at: Optional[datetime]


class RunDetailOut(RunOut):
    steps: List[StepOut]
    #: 引擎里的当前 state。以引擎为准，不是业务表里那份摘要
    state: Dict[str, Any]


class StartIn(BaseModel):
    template_key: str
    case_id: int
    payload: Dict[str, Any] = Field(default_factory=dict)


class ResumeIn(BaseModel):
    answer: str = Field(description="律师的确认内容 —— 会留痕，事后追责看它")
    #: 结构化修正：改抽错的日期、去掉不要的争议焦点…
    #: ★ 人说了算 —— 机器抽出来的一律可以被覆盖
    data: Dict[str, Any] = Field(default_factory=dict)


class CancelIn(BaseModel):
    note: str = ""


# ---------------------------------------------------------------- 接口


@router.get("/templates")
def templates(
    principal: Principal = Depends(require_perm("workflow.view")),
) -> List[Dict[str, Any]]:
    """模板清单，带人话名字和步骤 —— 界面要画进度条。"""
    return service.describe_templates()


@router.post("/runs", response_model=RunDetailOut)
def start_run(
    payload: StartIn,
    request: Request,
    principal: Principal = Depends(require_perm("workflow.start")),
    session: Session = Depends(get_session),
) -> RunDetailOut:
    row = service.start(session, principal, payload.template_key,
                        payload.case_id, payload.payload)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="workflow.start", resource_type="case",
                resource_id=str(payload.case_id), ip=client_ip(request),
                detail={"run_id": row.run_id, "template": payload.template_key})
    return _detail(session, principal, row)


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(
    run_id: str,
    principal: Principal = Depends(require_perm("workflow.view")),
    session: Session = Depends(get_session),
) -> RunDetailOut:
    return _detail(session, principal, service.get_run(session, principal, run_id))


@router.post("/runs/{run_id}/resume", response_model=RunDetailOut)
def resume_run(
    run_id: str,
    payload: ResumeIn,
    request: Request,
    principal: Principal = Depends(require_perm("workflow.resume")),
    session: Session = Depends(get_session),
) -> RunDetailOut:
    """回答人工确认点。**责任落点** —— 谁点的确认要记下来。"""
    row = service.resume(session, principal, run_id, payload.answer, payload.data)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="workflow.resume", resource_type="case",
                resource_id=str(row.case_id), ip=client_ip(request),
                detail={"run_id": run_id, "answer": payload.answer,
                        "data": payload.data, "status": row.status})
    return _detail(session, principal, row)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(
    run_id: str,
    payload: CancelIn,
    request: Request,
    principal: Principal = Depends(require_perm("workflow.cancel")),
    session: Session = Depends(get_session),
) -> RunOut:
    row = service.cancel(session, principal, run_id, payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="workflow.cancel", resource_type="case",
                resource_id=str(row.case_id), ip=client_ip(request),
                detail={"run_id": run_id, "note": payload.note})
    return RunOut.model_validate(row)


@router.get("/cases/{case_id}/runs", response_model=List[RunOut])
def case_runs(
    case_id: int,
    principal: Principal = Depends(require_perm("workflow.view")),
    session: Session = Depends(get_session),
) -> List[RunOut]:
    return [RunOut.model_validate(r)
            for r in service.runs_of_case(session, principal, case_id)]


# ---------------------------------------------------------------- 内部


def _detail(session: Session, principal: Principal, row) -> RunDetailOut:
    return RunDetailOut(
        **RunOut.model_validate(row).model_dump(),
        steps=[StepOut.model_validate(s) for s in service.steps_of(session, row.run_id)],
        state=service.live_state(session, principal, row.run_id),
    )


RunDetailOut.model_rebuild()
