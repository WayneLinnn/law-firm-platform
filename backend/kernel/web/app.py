"""FastAPI 装配。模块路由按拓扑序自动挂载，加模块不用改这里。"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from kernel import __version__
from kernel.bootstrap import bootstrap
from kernel.config import settings
from kernel.registry import registry
from kernel.web.errors import register_error_handlers

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    bootstrap()

    app = FastAPI(
        title="律所平台 API",
        version=__version__,
        debug=settings.debug,
    )
    register_error_handlers(app)

    @app.get("/health", tags=["kernel"])
    def health() -> dict:
        return {
            "status": "ok",
            "env": settings.env,
            "kernel": __version__,
            "modules": registry.describe(),
        }

    _mount_modules(app)
    _record_module_state()
    return app


def _mount_modules(app: FastAPI) -> None:
    for name in registry.order:
        manifest = registry.manifests[name]
        router_mod = registry.submodule(name, "router")
        if router_mod is None:
            logger.info("模块 %s 无 router.py，跳过挂载", name)
            continue
        router = getattr(router_mod, "router", None)
        if router is None:
            logger.warning("模块 %s 的 router.py 未定义 router 变量", name)
            continue
        app.include_router(router, prefix=manifest.prefix, tags=[name])
        logger.info("挂载模块路由 %s -> %s", name, manifest.prefix)


def _record_module_state() -> None:
    """把当前装载的模块写进 kernel.module_state。无数据库时静默跳过。"""
    if not settings.record_module_state:
        return
    try:
        from kernel.db.session import session_scope
        from sqlalchemy import text

        with session_scope() as session:
            for name in registry.order:
                manifest = registry.manifests[name]
                session.execute(
                    text(
                        "INSERT INTO kernel.module_state (name, version, first_seen_at, last_seen_at) "
                        "VALUES (:n, :v, now(), now()) "
                        "ON CONFLICT (name) DO UPDATE SET version = :v, last_seen_at = now()"
                    ),
                    {"n": name, "v": manifest.version},
                )
    except Exception as exc:  # 数据库未就绪不影响启动
        logger.warning("跳过 module_state 记录（数据库不可用）: %s", exc)


app = create_app()
