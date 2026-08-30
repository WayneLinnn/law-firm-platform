from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.analytics import service

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm


@router.get("/catalog")
def catalog(
    principal: Principal = Depends(require_perm("analytics.view")),
) -> List[Dict[str, Any]]:
    """看板上**我看得到**的指标有哪些 —— 前端据此渲染，不写死。"""
    return service.catalog(principal)


@router.get("/overview")
def overview(
    scope: str = Query(default="mine", description="mine / team / firm"),
    org_id: int = Query(default=0),
    since: Optional[date] = Query(default=None),
    until: Optional[date] = Query(default=None),
    principal: Principal = Depends(require_perm("analytics.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """看板数据。默认口径是**本月**（律所按月结算，不是"最近 30 天"）。

    ★ 拿不到的模块列在 `missing` 里 —— 界面要标"查不到"，
      **不能显示 0**：一个模块挂了显示"本月回款 0 元"会吓死人。
    """
    return service.overview(session, principal, scope, org_id or None,
                            since, until)


@router.get("/inbox")
def inbox(
    principal: Principal = Depends(require_perm("analytics.view")),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """统一待办中心：**所有等我动手的事，一个列表。**

    ★ 待办原来散在三个地方（待办页、单据页、工作台）——
      律师早上要点三个地方才知道今天有什么会出事，于是就不点了。
    """
    return service.inbox(session, principal)
