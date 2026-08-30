"""统一异常映射：业务模块只管抛 DomainError，Web 层负责转 HTTP。"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from kernel.errors import DomainError, KernelError

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=getattr(exc, "http_status", 400),
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(KernelError)
    def _kernel(request: Request, exc: KernelError) -> JSONResponse:
        logger.exception("内核错误")
        return JSONResponse(
            status_code=500, content={"code": "kernel_error", "message": str(exc)}
        )
