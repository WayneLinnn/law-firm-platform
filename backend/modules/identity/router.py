from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from kernel import audit
from kernel.db.session import get_session
from kernel.security import Principal
from modules.identity import access, service
from modules.identity.deps import (
    client_ip,
    current_principal,
    current_user,
    require_perm,
)
from modules.identity.models import User

router = APIRouter()


# ---------------------------------------------------------------- DTO


class LoginIn(BaseModel):
    tenant_code: str = Field(description="律所编码")
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    email: str
    phone: str
    is_active: bool
    is_superuser: bool
    # ★ 执业资格跟着人一起返回 —— 界面上任何列律师的地方都该能看到，
    #   而不是要点进详情才发现这人的证注销了。
    license_no: str = ""
    license_status: str = ""
    license_valid_to: Optional[date] = None
    license_firm: str = ""


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    category: str
    rank: int
    is_partner: bool
    can_bill: bool
    can_lead_team: bool
    can_handle_case: bool


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    org_unit_id: int
    org_role: str
    is_primary: bool
    valid_from: date
    valid_to: Optional[date]


class MeOut(BaseModel):
    user: UserOut
    position: Optional[PositionOut]      # 职级跟人走
    memberships: List[MembershipOut]     # 角色跟组织走
    org_ids: List[int]
    #: 权限点清单，前端据此决定显示哪些按钮（后端仍会二次校验）
    permissions: List[str]
    #: 权限点 -> 数据范围
    scopes: dict


class OrgUnitIn(BaseModel):
    name: str
    code: str
    kind: str = "department"
    parent_id: Optional[int] = None
    sort_order: int = 0


class OrgUnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: Optional[int]
    kind: str
    name: str
    code: str
    sort_order: int
    is_active: bool


class UserIn(BaseModel):
    username: str
    full_name: str
    password: str
    email: str = ""
    phone: str = ""


class MembershipIn(BaseModel):
    user_id: int
    org_unit_id: int
    org_role: str = ""
    is_primary: bool = False
    valid_from: Optional[date] = None


# ---------------------------------------------------------------- 认证


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, session: Session = Depends(get_session)) -> TokenOut:
    user = service.authenticate(session, payload.tenant_code, payload.username, payload.password)
    return TokenOut(access_token=service.issue_token(user))


@router.get("/me", response_model=MeOut)
def me(
    user: User = Depends(current_user),
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> MeOut:
    memberships = service.memberships_of(session, user.id)
    position = service.position_of(session, user.id)
    return MeOut(
        user=UserOut.model_validate(user),
        position=PositionOut.model_validate(position) if position else None,
        memberships=[MembershipOut.model_validate(m) for m in memberships],
        org_ids=service.org_ids_of_user(session, user.id),
        permissions=sorted(principal.permissions),
        scopes=dict(principal.scopes),
    )


@router.get("/positions", response_model=List[PositionOut])
def list_positions(
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[PositionOut]:
    return [PositionOut.model_validate(p)
            for p in service.list_positions(session, principal.tenant_id)]


@router.get("/user-positions")
def user_positions(
    at: Optional[date] = Query(default=None, description="查某时间点的职级，默认今天"),
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> dict:
    """全所人员在某时间点的职级，前端一次拿全，避免 N 次请求。"""
    from sqlalchemy import select as _select

    from modules.identity.models import User as UserModel

    out = {}
    for u in session.scalars(
        _select(UserModel).where(UserModel.tenant_id == principal.tenant_id)
    ):
        p = service.position_of(session, u.id, at=at)
        out[str(u.id)] = PositionOut.model_validate(p).model_dump() if p else None
    return out


# ---------------------------------------------------------------- 组织


@router.get("/org-units", response_model=List[OrgUnitOut])
def list_org_units(
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[OrgUnitOut]:
    units = service.list_org_units(session, principal.tenant_id)
    return [OrgUnitOut.model_validate(u) for u in units]


@router.post("/org-units", response_model=OrgUnitOut)
def create_org_unit(
    payload: OrgUnitIn,
    principal: Principal = Depends(require_perm("identity.manage_org")),
    session: Session = Depends(get_session),
) -> OrgUnitOut:
    unit = service.create_org_unit(
        session, principal.tenant_id, payload.name, payload.code,
        kind=payload.kind, parent_id=payload.parent_id, sort_order=payload.sort_order,
    )
    return OrgUnitOut.model_validate(unit)


@router.post("/org-units/{org_unit_id}/move", response_model=OrgUnitOut)
def move_org_unit(
    org_unit_id: int,
    new_parent_id: Optional[int] = Query(default=None),
    principal: Principal = Depends(require_perm("identity.manage_org")),
    session: Session = Depends(get_session),
) -> OrgUnitOut:
    return OrgUnitOut.model_validate(
        service.move_org_unit(session, org_unit_id, new_parent_id)
    )


@router.get("/org-units/{org_unit_id}/members", response_model=List[MembershipOut])
def org_members(
    org_unit_id: int,
    at: Optional[date] = Query(default=None, description="查某个时间点的成员，默认今天"),
    include_descendants: bool = Query(default=False),
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[MembershipOut]:
    rows = service.members_of(session, org_unit_id, at=at, include_descendants=include_descendants)
    return [MembershipOut.model_validate(m) for m in rows]


# ---------------------------------------------------------------- 用户与归属


@router.get("/users", response_model=List[UserOut])
def list_users(
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[UserOut]:
    from sqlalchemy import select

    from modules.identity.models import User as UserModel

    rows = session.scalars(
        select(UserModel)
        .where(UserModel.tenant_id == principal.tenant_id)
        .order_by(UserModel.id)
    )
    return [UserOut.model_validate(u) for u in rows]


@router.post("/users", response_model=UserOut)
def create_user(
    payload: UserIn,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage_user")),
    session: Session = Depends(get_session),
) -> UserOut:
    created = service.create_user(
        session, principal.tenant_id, payload.username, payload.full_name,
        payload.password, email=payload.email, phone=payload.phone,
    )
    audit.write(
        session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
        action="identity.create_user", resource_type="user",
        resource_id=str(created.id), ip=client_ip(request),
        detail={"username": created.username, "full_name": created.full_name},
    )
    return UserOut.model_validate(created)


@router.get("/users/{user_id}/memberships", response_model=List[MembershipOut])
def user_memberships(
    user_id: int,
    at: Optional[date] = Query(default=None, description="查某个时间点的归属，默认今天"),
    principal: Principal = Depends(require_perm("identity.view")),
    session: Session = Depends(get_session),
) -> List[MembershipOut]:
    rows = service.memberships_of(session, user_id, at=at)
    return [MembershipOut.model_validate(m) for m in rows]


@router.post("/memberships", response_model=MembershipOut)
def assign_membership(
    payload: MembershipIn,
    principal: Principal = Depends(require_perm("identity.manage_user")),
    session: Session = Depends(get_session),
) -> MembershipOut:
    m = service.assign_membership(
        session, payload.user_id, payload.org_unit_id,
        org_role=payload.org_role, is_primary=payload.is_primary,
        valid_from=payload.valid_from,
    )
    return MembershipOut.model_validate(m)


@router.post("/memberships/{membership_id}/end", response_model=MembershipOut)
def end_membership(
    membership_id: int,
    end_on: Optional[date] = Query(default=None, description="调岗生效日，默认今天"),
    principal: Principal = Depends(require_perm("identity.manage_user")),
    session: Session = Depends(get_session),
) -> MembershipOut:
    return MembershipOut.model_validate(service.end_membership(session, membership_id, end_on))


# ---------------------------------------------------------------- 隔离墙


class WallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resource_type: str
    resource_id: str
    user_id: int
    mode: str
    reason: str
    created_by: Optional[int]


@router.get("/walls", response_model=List[WallOut])
def list_walls(
    resource_type: str = Query(description="资源类型，目前只有 case"),
    resource_id: str = Query(description="资源 id"),
    principal: Principal = Depends(require_perm("identity.manage_wall")),
    session: Session = Depends(get_session),
) -> List[WallOut]:
    """看某个资源上挂了哪些墙。

    ★ 刻意**不检查该资源对调用者是否可见** —— 墙的作用就是让资源不可见，
      要求先能看见才能查墙，等于墙一设上就永远查不了。
    """
    rows = access.walls_on(session, principal.tenant_id, resource_type, resource_id)
    return [WallOut.model_validate(w) for w in rows]


@router.delete("/walls/{wall_id}", response_model=WallOut)
def remove_wall(
    wall_id: int,
    request: Request,
    principal: Principal = Depends(require_perm("identity.manage_wall")),
    session: Session = Depends(get_session),
) -> WallOut:
    """撤掉一堵墙。撤墙本身要留痕 —— 谁把谁放回来了，同样是合规问题。"""
    entry = access.remove_wall(session, principal.tenant_id, wall_id)
    out = WallOut.model_validate(entry)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="identity.wall_removed", resource_type=entry.resource_type,
                resource_id=entry.resource_id, ip=client_ip(request),
                detail={"wall_id": wall_id, "user_id": entry.user_id,
                        "mode": entry.mode, "reason": entry.reason})
    return out


# ---------------------------------------------------------------- 管理后台

# 角色权限、职级字典、晋升、审计 —— 见 router_admin.py
from modules.identity.router_admin import router as _admin_router  # noqa: E402

router.include_router(_admin_router)
