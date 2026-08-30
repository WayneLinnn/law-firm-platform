"""FastAPI 依赖：从请求里解出当前用户。

其他模块这样用（不 import 本文件，走 api.py）：

    identity = registry.api("identity")

    @router.get("/cases")
    def index(user = Depends(identity.current_user), session = Depends(get_session)):
        ...

Step 4 会在这之上加权限点与 DataScope 过滤；本步只解决"你是谁"。
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from kernel.db.session import get_session
from kernel.errors import NotFound, PermissionDenied, Unauthenticated
from kernel.security import Principal, require
from modules.identity.models import User
from modules.identity.service import decode_token, get_user


def _user_from_token(session: Session, token: str) -> User:
    """token 里的用户 id 查不到 —— 说明凭证已失效（用户被删、库被重建），
    这是 401 不是 404。返回 404 会让前端卡在空白页而不是跳登录。"""
    payload = decode_token(token)
    try:
        return get_user(session, int(payload["sub"]))
    except (NotFound, ValueError, KeyError):
        raise Unauthenticated("登录凭证已失效，请重新登录", code="identity.stale_token")


def _token_from(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def optional_user(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    token = _token_from(request)
    if not token:
        return None
    try:
        user = _user_from_token(session, token)
    except Unauthenticated:
        return None
    return user if user.is_active else None


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    token = _token_from(request)
    if not token:
        raise Unauthenticated("请先登录", code="identity.unauthenticated")
    user = _user_from_token(session, token)
    if not user.is_active:
        raise Unauthenticated("账号已停用", code="identity.user_inactive")
    return user


def current_user_id(user: User = Depends(current_user)) -> int:
    return user.id


def current_principal(
    user: User = Depends(current_user), session: Session = Depends(get_session)
) -> Principal:
    """当前请求者的权限快照。

    每次请求现装 —— 授权变更立即生效，不用等 token 过期。
    """
    from modules.identity.access import build_principal

    return build_principal(session, user.id)


def require_perm(permission: str) -> Callable[..., Principal]:
    """依赖工厂：没有该权限点就 403，有则返回 Principal 供后续做数据范围过滤。

        @router.post("/users")
        def create(p = Depends(identity.require_perm("identity.manage_user"))): ...
    """

    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        require(principal, permission)
        return principal

    return _dep


def client_ip(request: Request) -> str:
    """审计用。有反向代理时优先取 X-Forwarded-For 的第一段。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
