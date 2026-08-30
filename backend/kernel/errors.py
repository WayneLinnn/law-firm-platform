"""内核异常。业务模块抛这些，Web 层统一转成 HTTP 响应。"""
from __future__ import annotations


class KernelError(Exception):
    """内核层错误基类。"""


class ModuleError(KernelError):
    """模块装载相关错误。"""


class ModuleNotEnabled(ModuleError):
    """请求了一个未启用/不存在的模块。"""


class ModuleDependencyError(ModuleError):
    """必需依赖缺失。"""


class ModuleCycleError(ModuleError):
    """模块依赖成环。"""


class DomainError(KernelError):
    """业务规则错误 —— 会被映射为 HTTP 400。"""

    http_status = 400

    def __init__(self, message: str, code: str = "domain_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class NotFound(DomainError):
    http_status = 404

    def __init__(self, message: str = "资源不存在", code: str = "not_found"):
        super().__init__(message, code)


class Unauthenticated(DomainError):
    """没登录 / 凭证无效 —— HTTP 401，前端据此跳登录页。

    与 PermissionDenied(403) 区分：401 是"你是谁我不知道"，
    403 是"我知道你是谁，但你不能看这个"。前端处理方式完全不同。
    """

    http_status = 401

    def __init__(self, message: str = "请先登录", code: str = "unauthenticated"):
        super().__init__(message, code)


class PermissionDenied(DomainError):
    http_status = 403

    def __init__(self, message: str = "无权访问", code: str = "permission_denied"):
        super().__init__(message, code)
