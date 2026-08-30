"""DeepSeek 适配器 —— ModelPort 的实现。

只有 kernel/bootstrap.py 可以 import 本文件。业务模块一律通过
container.get("model") 拿到 ModelPort，永远不知道背后是谁。

事实核对（2026-08，官方 API Docs）：
  · base_url          https://api.deepseek.com （OpenAI 协议兼容）
  · 模型              deepseek-v4-flash / deepseek-v4-pro / deepseek-v4-flash-vision-exp
  · 上下文 1M，最大输出 384K
  · response_format   仅支持 text 与 json_object（**没有 json_schema 严格模式**）
  · thinking          {"type": "enabled"|"disabled", "reasoning_effort": "low"|"high"|"max"}
  · function calling  支持，最多 128 个函数
  · **没有 embeddings 端点** —— 向量走 EmbeddingPort，本地 bge（见 D2）

因为没有严格 schema 模式，结构化输出的做法是：
  开 json_object + 把 schema 写进系统提示 + Pydantic 校验 + 失败重试一次。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from kernel.config import Settings
from kernel.ports.model import ChatMessage, ModelResult

logger = logging.getLogger(__name__)

# 官方离峰价（USD / 1M tokens）。高峰时段翻倍：周一至周五 UTC 01:00-04:00、06:00-10:00。
# 这里只用于粗略记账，账单以官方为准。
PRICE_USD_PER_MTOK = {
    "deepseek-v4-flash": (0.22, 0.66),
    "deepseek-v4-pro": (0.66, 1.98),
    "deepseek-v4-flash-vision-exp": (0.22, 0.66),
}

_JSON_INSTRUCTION = (
    "你必须只输出一个 JSON 对象，不要输出任何解释、前后缀或 markdown 代码块。"
    "该 JSON 必须严格符合下面的 JSON Schema：\n{schema}"
)


class DeepSeekModel:
    """实现 kernel.ports.ModelPort。"""

    def __init__(self, settings: Settings, http_client: Any = None) -> None:
        from openai import OpenAI

        if not settings.deepseek_api_key:
            raise RuntimeError(
                "缺少 LFP_DEEPSEEK_API_KEY。请在 backend/.env 里填写；"
                "或设 LFP_MODEL_BACKEND=fake 以便无 key 本地开发。"
            )
        self._settings = settings
        self._default_model = settings.deepseek_model
        self._client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.model_timeout,
            max_retries=settings.model_max_retries,
            http_client=http_client,
        )

    # ------------------------------------------------------------------ 公开接口

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
        trace_id = uuid.uuid4().hex
        model_id = model or self._default_model
        payload = self._build_payload(
            messages, json_schema, thinking, effort, temperature, max_tokens, model_id
        )

        result = self._call(payload, trace_id, model_id)
        if json_schema is None:
            return result

        parsed = _parse_json(result.text)
        if parsed is None:
            # 只重试一次，并把错误回喂给模型 —— 再失败就抛，不要静默返回半成品
            logger.warning("[%s] JSON 解析失败，重试一次", trace_id)
            retry = list(payload["messages"]) + [
                {"role": "assistant", "content": result.text},
                {"role": "user", "content": "上面的输出不是合法 JSON，请只输出合法 JSON 对象。"},
            ]
            payload["messages"] = retry
            result = self._call(payload, trace_id, model_id)
            parsed = _parse_json(result.text)
            if parsed is None:
                raise ValueError(
                    "模型两次都未返回合法 JSON（trace_id={0}）".format(trace_id)
                )
        result.data = parsed
        return result

    # ------------------------------------------------------------------ 内部

    def _build_payload(
        self,
        messages: List[ChatMessage],
        json_schema: Optional[Dict[str, Any]],
        thinking: bool,
        effort: str,
        temperature: float,
        max_tokens: int,
        model_id: str,
    ) -> Dict[str, Any]:
        msgs = [{"role": m.role, "content": m.content} for m in messages]

        payload: Dict[str, Any] = {
            "model": model_id,
            "messages": msgs,
            "max_tokens": max_tokens,
        }

        if json_schema is not None:
            # 没有严格 schema 模式，只能 json_object + 提示里带 schema
            payload["response_format"] = {"type": "json_object"}
            msgs.insert(0, {
                "role": "system",
                "content": _JSON_INSTRUCTION.format(
                    schema=json.dumps(json_schema, ensure_ascii=False)
                ),
            })

        # thinking 是 DeepSeek 私有扩展，openai SDK 不认识标准之外的参数，
        # 必须走 extra_body（SDK 会把它合并进请求体顶层）。
        #
        # ★ 必须显式声明 disabled：V4 **默认开启思考**，不传等于默认开。
        #   实测同一问题：不传 88 output tokens / 显式 disabled 只要 50 —— 白花 76%。
        #   推理 token 也占输出配额，平台里绝大多数调用（分类、改写、格式化）
        #   不需要思考，一律关掉。
        if thinking:
            payload["extra_body"] = {
                "thinking": {"type": "enabled", "reasoning_effort": effort}
            }
        else:
            payload["extra_body"] = {"thinking": {"type": "disabled"}}
            # 思考模式下 temperature 等采样参数无效，只在关闭思考时传
            payload["temperature"] = temperature

        return payload

    def _call(self, payload: Dict[str, Any], trace_id: str, model_id: str) -> ModelResult:
        resp = self._client.chat.completions.create(**payload)
        choice = resp.choices[0]
        text = choice.message.content or ""
        reasoning = getattr(choice.message, "reasoning_content", None)

        usage = _usage_of(resp, model_id)
        logger.info(
            "[%s] model=%s in=%s out=%s ~$%.5f",
            trace_id, model_id, usage.get("input_tokens"),
            usage.get("output_tokens"), usage.get("cost_usd", 0.0),
        )
        if not text.strip():
            # 典型原因：开了思考但 max_tokens 太小，推理 token 把配额吃光，
            # 正文返回空串。这是静默失败，必须喊出来。
            reason = "推理占满输出配额" if reasoning else "模型返回空内容"
            logger.warning(
                "[%s] 正文为空（%s）：finish_reason=%s max_tokens=%s，请调大 max_tokens",
                trace_id, reason, choice.finish_reason, payload.get("max_tokens"),
            )
        return ModelResult(
            text=text,
            reasoning=reasoning,
            usage=usage,
            model=model_id,
            trace_id=trace_id,
        )


# ---------------------------------------------------------------------- 工具函数


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """容忍模型偶尔套 markdown 代码块。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw[3:]
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    raw = raw.strip()
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _usage_of(resp: Any, model_id: str) -> Dict[str, Any]:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    tin = getattr(u, "prompt_tokens", 0) or 0
    tout = getattr(u, "completion_tokens", 0) or 0
    price_in, price_out = PRICE_USD_PER_MTOK.get(model_id, (0.0, 0.0))
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details else 0
    return {
        "input_tokens": tin,
        "output_tokens": tout,
        "cached_tokens": cached,
        # 离峰估算，高峰翻倍；仅供内部记账与配额，不作账单依据
        "cost_usd": round((tin * price_in + tout * price_out) / 1e6, 6),
    }


def build_model_adapter(settings: Settings, http_client: Any = None) -> Any:
    """给 bootstrap 用的工厂。backend=fake 时返回假实现，无 key 也能跑。"""
    if settings.model_backend == "fake":
        from adapters.model.fake import FakeModel

        return FakeModel()
    return DeepSeekModel(settings, http_client=http_client)
