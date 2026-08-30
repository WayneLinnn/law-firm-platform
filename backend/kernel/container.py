"""极简依赖容器：把 ports 契约绑定到 adapters 实现。

业务模块这样用：
    from kernel.container import container
    model = container.get("model")        # 拿到 ModelPort 实现
永远不会出现 `from adapters.model.deepseek import ...`。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from kernel.errors import KernelError


class Container:
    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._instances: Dict[str, Any] = {}

    def bind(self, key: str, factory: Callable[[], Any], singleton: bool = True) -> None:
        self._factories[key] = factory
        self._instances.pop(key, None)
        if not singleton:
            self._instances.pop(key, None)

    def get(self, key: str) -> Any:
        if key in self._instances:
            return self._instances[key]
        factory = self._factories.get(key)
        if factory is None:
            raise KernelError(
                "未绑定的 port: {0}（应在 kernel/bootstrap.py 中绑定实现）".format(key)
            )
        instance = factory()
        self._instances[key] = instance
        return instance

    def get_optional(self, key: str) -> Optional[Any]:
        if key not in self._factories:
            return None
        return self.get(key)

    def has(self, key: str) -> bool:
        return key in self._factories

    def reset(self) -> None:
        self._factories.clear()
        self._instances.clear()


container = Container()
