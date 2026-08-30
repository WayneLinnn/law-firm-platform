"""假模型 —— 无 API Key 时的本地开发与 CI 用。

设 LFP_MODEL_BACKEND=fake 即可启用。它不联网、不花钱、结果确定，
让业务模块的测试不依赖外部服务。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from kernel.ports.model import ChatMessage, ModelResult


def _stub_for(schema: Dict[str, Any]) -> Dict[str, Any]:
    """按 JSON Schema 造一个类型正确的最小对象，供测试断言形状。"""
    out: Dict[str, Any] = {}
    for key, spec in (schema.get("properties") or {}).items():
        if "default" in spec:
            out[key] = spec["default"]
            continue
        kind = spec.get("type")
        out[key] = {
            "string": "", "integer": 0, "number": 0.0,
            "boolean": False, "array": [], "object": {},
        }.get(kind, None)
    return out


class FakeModel:
    """实现 kernel.ports.ModelPort。"""

    def __init__(self, reply: str = "（假模型回复）") -> None:
        self.reply = reply
        self.calls: List[Dict[str, Any]] = []

    def chat(
        self,
        messages: List[ChatMessage],
        *,
        json_schema: Optional[Dict[str, Any]] = None,
        thinking: bool = False,
        effort: str = "high",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> ModelResult:
        self.calls.append({
            "messages": [(m.role, m.content) for m in messages],
            "json_schema": json_schema,
            "thinking": thinking,
            "effort": effort,
            "model": model,
        })
        data = _stub_for(json_schema) if json_schema else None
        text = json.dumps(data, ensure_ascii=False) if data is not None else self.reply
        return ModelResult(
            text=text,
            data=data,
            usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            model="fake",
            trace_id=uuid.uuid4().hex,
        )
