"""事件总线契约 —— 【预留，Step 1 不实现】。

现阶段模块间用直接调用（registry.api / optional_api）。
调用点在 service.py 里用 `# EVENT: xxx` 注释标记，
等模块数量超过 8 个或要拆服务时，把这些点换成 publish() 即可。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@dataclass
class Event:
    name: str  # 形如 case.created / doc.signed / case.closed
    payload: Dict[str, Any] = field(default_factory=dict)
    tenant_id: str = ""
    actor_id: str = ""


@runtime_checkable
class EventPort(Protocol):
    def publish(self, event: Event) -> None:
        ...

    def subscribe(self, name: str, handler: Callable[[Event], None]) -> None:
        ...


class NullEventBus:
    """空实现：记录但不投递。让预留接口现在就能被安全调用。"""

    def __init__(self) -> None:
        self.published: List[Event] = []

    def publish(self, event: Event) -> None:
        self.published.append(event)

    def subscribe(self, name: str, handler: Callable[[Event], None]) -> None:
        return None
