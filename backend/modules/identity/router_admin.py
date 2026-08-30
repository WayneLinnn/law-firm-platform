"""管理后台接口：角色权限、职级、晋升、审计。

与 router.py 的分工：
    router.py        登录、组织、人员、归属 —— 日常业务要用的
    router_admin.py  角色权限、职级字典、晋升、审计 —— 管理后台专用

**这里只做 CRUD 和查询，任何业务规则都在 service.py / access.py 里。**
管理页不许有自己的业务逻辑，否则会出现"只有后台能做的操作"，
业务规则一半在 service 一半在管理页，最后谁都不敢动。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import DataScope, Principal
from modules.identity import access, service
from modules.identity.deps import client_ip, require_perm

router = APIRouter()


# ---------------------------------------------------------------- DTO


class PermissionOut(BaseModel):
    code: str
    module: str


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    data_scope: str
    description: str
    is_active: bool


class RoleDetailOut(RoleOut):
    permissions: List[str]


class RoleIn(BaseModel):
    code: str
    name: str
    data_scope: str = DataScope.SELF
    description: str = ""
    permissions: List[str] = Field(default_factory=list)


class RolePermissionsIn(BaseModel):
    permissions: List[str]


class RoleAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    role_id: int
    org_unit_id: Optional[int]
    valid_from: date
    valid_to: Optional[date]


class RoleAssignmentIn(BaseModel):
    user_id: int
    role_code: str
    org_unit_id: Optional[int] = None
    valid_from: Optional[date] = None


class PositionIn(BaseModel):
    code: str
    name: str
    category: str = "lawyer"
    rank: int = 0
    is_partner: bool = False
    can_bill: bool = False
    can_lead_team: bool = False
    can_handle_case: bool = False


class PositionPatch(BaseModel):
    name: Optional[str] = None
    rank: Optional[int] = None
    is_partner: Optional[bool] = None
    can_bill: Optional[bool] = None
    can_lead_team: Optional[bool] = None
    can_handle_case: Optional[bool] = None
    is_active: Optional[bool] = None


class AppointmentIn(BaseModel):
    position_code: str
    valid_from: Optional[date] = None
    note: str = ""


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    position_id: int
    valid_from: date
    valid_to: Optional[date]
    note: str


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: Optional[int]
    action: str
    resource_type: str
    resource_id: str
    scope: str
    detail: str
    ip: str
    created_at: object


# ---------------------------------------------------------------- 权限点


@router.get("/permissions", response_model=List[PermissionOut])
def list_permissions(
    _: Principal = Depends(require_perm("identity.manage_role")),
) -> List[PermissionOut]:
    """全部已注册的权限点。**由各模块 manifest 声明，加模块自动多出可选项。**"""
    return [
        PermissionOut(code=p, module=p.split(".")[0])
        for p in registry.permissions()
    ]


# ---------------------------------------------------------------- 角色


@router.get("/roles", response_model=List[RoleDetailOut])
def list_roles(
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[RoleDetailOut]:
    out = []
    for role in access.list_roles(session, principal.tenant_id):
        out.append(RoleDetailOut(
            **RoleOut.model_validate(role).model_dump(),
            permissions=access.role_permissions(session, role.id),
        ))
    return out


@router.post("/roles", response_model=RoleDetailOut)
def create_role(
    payload: RoleIn,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage_role")),
    session: Session = Depends(get_session),
) -> RoleDetailOut:
    role = access.create_role(
        session, principal.tenant_id, payload.code, payload.name,
        payload.data_scope, payload.description, payload.permissions,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.create_role", resource_type="role",
                resource_id=str(role.id), ip=client_ip(request),
                detail={"code": role.code, "scope": role.data_scope,
                        "permissions": list(payload.permissions)})
    return RoleDetailOut(
        **RoleOut.model_validate(role).model_dump(),
        permissions=access.role_permissions(session, role.id),
    )


@router.put("/roles/{code}/permissions", response_model=RoleDetailOut)
def set_role_permissions(
    code: str,
    payload: RolePermissionsIn,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage_role")),
    session: Session = Depends(get_session),
) -> RoleDetailOut:
    role = access.get_role(session, principal.tenant_id, code)
    before = access.role_permissions(session, role.id)
    access.set_role_permissions(session, role.id, payload.permissions)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.set_role_permissions", resource_type="role",
                resource_id=str(role.id), ip=client_ip(request),
                detail={"code": code, "before": before, "after": payload.permissions})
    return RoleDetailOut(
        **RoleOut.model_validate(role).model_dump(),
        permissions=access.role_permissions(session, role.id),
    )


# ---------------------------------------------------------------- 授权


@router.get("/users/{user_id}/roles", response_model=List[RoleAssignmentOut])
def user_roles(
    user_id: int,
    at: Optional[date] = Query(default=None),
    _: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[RoleAssignmentOut]:
    return [RoleAssignmentOut.model_validate(a)
            for a in access.roles_of(session, user_id, at=at)]


@router.post("/role-assignments", response_model=RoleAssignmentOut)
def assign_role(
    payload: RoleAssignmentIn,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage_role")),
    session: Session = Depends(get_session),
) -> RoleAssignmentOut:
    assignment = access.assign_role(
        session, payload.user_id, payload.role_code,
        org_unit_id=payload.org_unit_id, valid_from=payload.valid_from,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.assign_role", resource_type="user",
                resource_id=str(payload.user_id), ip=client_ip(request),
                detail={"role": payload.role_code, "org_unit_id": payload.org_unit_id})
    return RoleAssignmentOut.model_validate(assignment)


@router.post("/role-assignments/{assignment_id}/revoke", response_model=RoleAssignmentOut)
def revoke_role(
    assignment_id: int,
    request: Request,
    end_on: Optional[date] = Query(default=None),
    principal: Principal = Depends(require_perm("identity.manage_role")),
    session: Session = Depends(get_session),
) -> RoleAssignmentOut:
    assignment = access.revoke_role(session, assignment_id, end_on)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.revoke_role", resource_type="user",
                resource_id=str(assignment.user_id), ip=client_ip(request),
                detail={"assignment_id": assignment_id})
    return RoleAssignmentOut.model_validate(assignment)


# ---------------------------------------------------------------- 职级


@router.post("/positions")
def create_position(
    payload: PositionIn,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage_role")),
    session: Session = Depends(get_session),
) -> dict:
    position = service.create_position(
        session, principal.tenant_id, payload.code, payload.name,
        category=payload.category, rank=payload.rank,
        is_partner=payload.is_partner, can_bill=payload.can_bill,
        can_lead_team=payload.can_lead_team, can_handle_case=payload.can_handle_case,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.create_position", resource_type="position",
                resource_id=str(position.id), ip=client_ip(request),
                detail=payload.model_dump())
    return {"id": position.id, "code": position.code}


@router.patch("/positions/{code}")
def update_position(
    code: str,
    payload: PositionPatch,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage_role")),
    session: Session = Depends(get_session),
) -> dict:
    """改职级开关。**这是数据不是代码** —— 律所要放开助理牵头团队，改这里即可。"""
    position = service.get_position(session, principal.tenant_id, code)
    changed = {}
    for field, value in payload.model_dump(exclude_none=True).items():
        if getattr(position, field) != value:
            changed[field] = {"before": getattr(position, field), "after": value}
            setattr(position, field, value)
    session.flush()
    if changed:
        audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                    action="identity.update_position", resource_type="position",
                    resource_id=str(position.id), ip=client_ip(request),
                    detail={"code": code, "changed": changed})
    return {"code": position.code, "changed": list(changed)}


# ---------------------------------------------------------------- 晋升


@router.get("/users/{user_id}/appointments", response_model=List[AppointmentOut])
def appointment_history(
    user_id: int,
    _: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[AppointmentOut]:
    return [AppointmentOut.model_validate(a)
            for a in service.appointment_history(session, user_id)]


@router.post("/users/{user_id}/appointments", response_model=AppointmentOut)
def appoint(
    user_id: int,
    payload: AppointmentIn,
    request: Request,
    principal: Principal = Depends(require_perm("identity.promote")),
    session: Session = Depends(get_session),
) -> AppointmentOut:
    """入职定级 / 晋升。**管委会职权** —— 需要 identity.promote。"""
    before = service.position_of(session, user_id)
    appointment = service.appoint(
        session, user_id, payload.position_code,
        valid_from=payload.valid_from, note=payload.note,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.promote", resource_type="user",
                resource_id=str(user_id), ip=client_ip(request),
                detail={"from": before.code if before else None,
                        "to": payload.position_code,
                        "valid_from": str(appointment.valid_from),
                        "note": payload.note})
    return AppointmentOut.model_validate(appointment)


# ---------------------------------------------------------------- 审计


@router.get("/audit", response_model=List[AuditOut])
def read_audit(
    actor_id: Optional[int] = Query(default=None),
    action: Optional[str] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    resource_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_perm("audit.view")),
    session: Session = Depends(get_session),
) -> List[AuditOut]:
    rows = audit.read(
        session, principal.tenant_id, actor_id=actor_id, action=action,
        resource_type=resource_type, resource_id=resource_id, limit=limit,
    )
    return [AuditOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------- 执业资格


class LicenseIn(BaseModel):
    license_no: str = ""
    status: str = Field(default="",
                        description="practising 执业中 / intern 实习 / "
                                    "suspended 停止执业 / revoked 已注销")
    licensed_on: Optional[date] = None
    valid_to: Optional[date] = Field(default=None, description="年检有效期至")
    firm: str = Field(default="", description="执业机构")


@router.get("/users/{user_id}/license")
def get_license(
    user_id: int,
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """一个人的执业资格现状，含"要不要去核实"的提示。"""
    return service.license_of(session, user_id)


@router.put("/users/{user_id}/license")
def put_license(
    user_id: int,
    payload: LicenseIn,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """登记 / 更新执业资格。

    ★ 要 `identity.manage` —— 律师**不能自己改自己的执业状态**。
      能自己把"已注销"改回"执业中"，这道检查就等于没有。
    """
    user = service.set_license(
        session, user_id,
        license_no=payload.license_no, status=payload.status,
        licensed_on=payload.licensed_on, valid_to=payload.valid_to,
        firm=payload.firm,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.set_license", resource_type="user",
                resource_id=str(user.id), ip=client_ip(request),
                detail=payload.model_dump(mode="json"))
    return service.license_of(session, user_id)
