from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.task import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    basis: str
    cause_code: str
    role: str
    anchor_field: str
    months: int
    days: int
    fatal: bool
    suspendable: bool
    outer_years: int
    warn_days: int
    caveat: str
    note: str
    builtin: bool
    active: bool


class RuleIn(BaseModel):
    code: str
    name: str
    #: ★ 必填 —— 算不出来路的期限没人敢用
    basis: str
    anchor_field: str
    cause_code: str = ""
    role: str = ""
    months: int = 0
    days: int = 0
    fatal: bool = False
    suspendable: bool = False
    outer_years: int = Field(default=0, description="最长保护期（年），0 = 不适用")
    warn_days: int = 30
    caveat: str = ""
    note: str = ""


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: Optional[int]
    title: str
    detail: str
    assignee_id: Optional[int]
    due_on: Optional[date]
    priority: str
    status: str
    deadline_id: Optional[int]
    created_by: int
    done_at: Optional[datetime]
    created_at: datetime


class TaskIn(BaseModel):
    title: str
    case_id: Optional[int] = None
    detail: str = ""
    assignee_id: Optional[int] = None
    due_on: Optional[date] = None
    priority: str = "normal"
    deadline_id: Optional[int] = None


class TaskPatch(BaseModel):
    title: Optional[str] = None
    detail: Optional[str] = None
    assignee_id: Optional[int] = None
    due_on: Optional[date] = None
    priority: Optional[str] = None
    status: Optional[str] = None


class AnchorIn(BaseModel):
    anchor_on: Optional[date] = None
    note: str = ""


class InterruptIn(BaseModel):
    on: date
    note: str = Field(description="中断事由与证据 —— 事后要拿得出来")


class SuspendIn(BaseModel):
    #: ★ 事由**消除**之日，不是发生之日 —— 194 条的算式只用得上消除日
    end_on: date = Field(description="中止事由消除之日")
    note: str = Field(description="障碍事由与证据 —— 事后要拿得出来")


class MarkIn(BaseModel):
    status: str = Field(description="done 已行使 / waived 不适用")
    note: str = ""


class ManualDeadlineIn(BaseModel):
    case_id: int
    name: str
    basis: str
    due_on: date
    fatal: bool = False
    note: str = ""


# ---------------------------------------------------------------- 规则


@router.get("/rules", response_model=List[RuleOut])
def list_rules(
    cause_code: str = Query(default=""),
    include_inactive: bool = Query(default=False),
    principal: Principal = Depends(require_perm("task.view")),
    session: Session = Depends(get_session),
) -> List[RuleOut]:
    return [RuleOut.model_validate(r)
            for r in service.list_rules(session, principal, cause_code, include_inactive)]


@router.post("/rules", response_model=RuleOut)
def create_rule(
    payload: RuleIn,
    request: Request,
    principal: Principal = Depends(require_perm("task.manage_rule")),
    session: Session = Depends(get_session),
) -> RuleOut:
    """律所自己加规则。**规则是数据不是代码**，加一条不用发版。"""
    row = service.create_rule(session, principal, **payload.model_dump())
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="task.rule_created", resource_type="rule", resource_id=payload.code,
                ip=client_ip(request), detail=payload.model_dump())
    return RuleOut.model_validate(row)


@router.post("/rules/{code}/active", response_model=RuleOut)
def set_active(
    code: str,
    active: bool = Query(default=True),
    principal: Principal = Depends(require_perm("task.manage_rule")),
    session: Session = Depends(get_session),
) -> RuleOut:
    return RuleOut.model_validate(service.set_rule_active(session, principal, code, active))


@router.post("/rules/seed", response_model=List[RuleOut])
def seed(
    principal: Principal = Depends(require_perm("task.manage_rule")),
    session: Session = Depends(get_session),
) -> List[RuleOut]:
    """把内置规则灌进本所。已存在的不覆盖 —— 律所可能改过。"""
    return [RuleOut.model_validate(r)
            for r in service.ensure_default_rules(session, principal.tenant_id)]


# ---------------------------------------------------------------- 时限


@router.get("/cases/{case_id}/deadlines")
def case_deadlines(
    case_id: int,
    principal: Principal = Depends(require_perm("task.view")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    return [service.describe(d)
            for d in service.deadlines_of(session, principal, case_id)]


@router.post("/cases/{case_id}/deadlines/generate")
def generate(
    case_id: int,
    role: str = Query(default=""),
    principal: Principal = Depends(require_perm("task.deadline")),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """按规则生成时限骨架（起算日期留空，等律师填）。"""
    rows = service.generate(session, principal, case_id, anchors={}, role=role)
    return [service.describe(d) for d in rows]


@router.post("/deadlines/{deadline_id}/anchor")
def set_anchor(
    deadline_id: int,
    payload: AnchorIn,
    request: Request,
    principal: Principal = Depends(require_perm("task.deadline")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """律师定起算日 —— 这个动作同时视为**确认**。"""
    row = service.set_anchor(session, principal, deadline_id,
                             payload.anchor_on, payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="task.deadline_anchor", resource_type="case",
                resource_id=str(row.case_id), ip=client_ip(request),
                detail={"deadline_id": deadline_id,
                        "anchor_on": str(payload.anchor_on), "note": payload.note})
    return service.describe(row)


@router.post("/deadlines/{deadline_id}/interrupt")
def interrupt(
    deadline_id: int,
    payload: InterruptIn,
    request: Request,
    principal: Principal = Depends(require_perm("task.deadline")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """登记时效中断。★ 除斥期间会被拒绝 —— 那是法律性质，不是配置。"""
    row = service.interrupt(session, principal, deadline_id, payload.on, payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="task.deadline_interrupt", resource_type="case",
                resource_id=str(row.case_id), ip=client_ip(request),
                detail={"deadline_id": deadline_id, "on": str(payload.on),
                        "note": payload.note})
    return service.describe(row)


@router.post("/deadlines/{deadline_id}/suspend")
def suspend(
    deadline_id: int,
    payload: SuspendIn,
    request: Request,
    principal: Principal = Depends(require_perm("task.deadline")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """登记时效中止（194 条）：事由消除之日起满六个月届满。

    ★ 和中断分开两个接口，不合并成一个带 kind 参数的 —— 两者算法不同、
      适用条件不同，合并只会让调用方填错那个参数。
    """
    row = service.suspend(session, principal, deadline_id,
                          payload.end_on, payload.note)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="task.deadline_suspend", resource_type="case",
                resource_id=str(row.case_id), ip=client_ip(request),
                detail={"deadline_id": deadline_id, "end_on": str(payload.end_on),
                        "note": payload.note})
    return service.describe(row)


@router.post("/deadlines/{deadline_id}/mark")
def mark(
    deadline_id: int,
    payload: MarkIn,
    principal: Principal = Depends(require_perm("task.deadline")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return service.describe(service.mark(session, principal, deadline_id,
                                         payload.status, payload.note))


@router.post("/deadlines")
def add_deadline(
    payload: ManualDeadlineIn,
    principal: Principal = Depends(require_perm("task.deadline")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """手工加一条规则覆盖不到的时限。"""
    return service.describe(service.add_deadline(
        session, principal, payload.case_id, payload.name, payload.basis,
        payload.due_on, payload.fatal, payload.note))


# ---------------------------------------------------------------- 任务


@router.post("/tasks", response_model=TaskOut)
def create_task(
    payload: TaskIn,
    principal: Principal = Depends(require_perm("task.manage")),
    session: Session = Depends(get_session),
) -> TaskOut:
    return TaskOut.model_validate(service.create_task(
        session, principal, payload.title, payload.case_id, payload.detail,
        payload.assignee_id, payload.due_on, payload.priority, payload.deadline_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskPatch,
    principal: Principal = Depends(require_perm("task.manage")),
    session: Session = Depends(get_session),
) -> TaskOut:
    return TaskOut.model_validate(service.update_task(
        session, principal, task_id,
        **{k: v for k, v in payload.model_dump().items() if v is not None}))


@router.get("/cases/{case_id}/tasks", response_model=List[TaskOut])
def case_tasks(
    case_id: int,
    principal: Principal = Depends(require_perm("task.view")),
    session: Session = Depends(get_session),
) -> List[TaskOut]:
    return [TaskOut.model_validate(t)
            for t in service.tasks_of_case(session, principal, case_id)]


@router.get("/board")
def board(
    user_id: int = Query(default=0),
    principal: Principal = Depends(require_perm("task.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """我的待办：任务 + 时限，一起看。"""
    b = service.board(session, principal, user_id or None)
    return {
        "tasks": [TaskOut.model_validate(t).model_dump() for t in b["tasks"]],
        "deadlines": [service.describe(d) for d in b["deadlines"]],
        "expired": b["expired"], "urgent": b["urgent"],
        "unknown": b["unknown"], "overdue_tasks": b["overdue_tasks"],
    }
