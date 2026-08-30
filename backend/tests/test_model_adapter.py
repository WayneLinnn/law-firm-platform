"""DeepSeek 适配器。

不需要真 API Key —— 用 httpx 的假传输层拦下请求，验证**我们发出去的
请求形状是否正确**（这才是适配器的职责），响应用官方文档的结构伪造。
"""
import json

import httpx
import pytest

from kernel.config import Settings
from kernel.ports.model import ChatMessage


def _settings(**kw):
    return Settings(
        deepseek_api_key="test-key",
        deepseek_base_url="https://api.deepseek.com",
        **kw,
    )


def _fake_transport(capture: dict, content: str = "你好", usage=None):
    def handler(request: httpx.Request) -> httpx.Response:
        capture["url"] = str(request.url)
        capture["auth"] = request.headers.get("authorization", "")
        capture["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": capture["body"]["model"],
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50,
                               "total_tokens": 150},
        })

    return httpx.MockTransport(handler)


def _build(capture, content="你好", **kw):
    from adapters.model.deepseek import DeepSeekModel

    client = httpx.Client(transport=_fake_transport(capture, content, kw.pop("usage", None)))
    return DeepSeekModel(_settings(**kw), http_client=client)


# ---------------------------------------------------------------- 基本调用

def test_hits_deepseek_endpoint_with_bearer_key():
    cap = {}
    model = _build(cap)
    model.chat([ChatMessage(role="user", content="你好")])
    assert cap["url"] == "https://api.deepseek.com/chat/completions"
    assert cap["auth"] == "Bearer test-key"


def test_default_model_is_flash():
    cap = {}
    _build(cap).chat([ChatMessage(role="user", content="hi")])
    assert cap["body"]["model"] == "deepseek-v4-flash"


def test_model_can_be_overridden_per_call():
    """难任务传更强的型号，不改配置。"""
    cap = {}
    _build(cap).chat([ChatMessage(role="user", content="hi")], model="deepseek-v4-pro")
    assert cap["body"]["model"] == "deepseek-v4-pro"


def test_returns_text_and_usage_with_cost():
    cap = {}
    r = _build(cap, content="答复内容").chat([ChatMessage(role="user", content="hi")])
    assert r.text == "答复内容"
    assert r.model == "deepseek-v4-flash"
    assert r.trace_id
    assert r.usage["input_tokens"] == 100
    assert r.usage["output_tokens"] == 50
    # 离峰价 flash: 0.22 / 0.66 per 1M
    assert r.usage["cost_usd"] == pytest.approx((100 * 0.22 + 50 * 0.66) / 1e6, rel=1e-6)


# ---------------------------------------------------------------- 思考模式

def test_thinking_off_must_disable_explicitly():
    """V4 默认开启思考，不显式关闭会白花输出 token（实测 88 vs 50）。"""
    cap = {}
    _build(cap).chat([ChatMessage(role="user", content="hi")], temperature=0.3)
    assert cap["body"]["temperature"] == 0.3
    assert cap["body"]["thinking"] == {"type": "disabled"}, (
        "必须显式 disabled —— 不传等于默认开思考，成本白涨"
    )


def test_thinking_on_omits_temperature():
    """官方明确：思考模式下 temperature/top_p 等采样参数无效，不该发出去。"""
    cap = {}
    _build(cap).chat([ChatMessage(role="user", content="hi")], thinking=True, effort="max")
    assert cap["body"]["thinking"] == {"type": "enabled", "reasoning_effort": "max"}
    assert "temperature" not in cap["body"]


# ---------------------------------------------------------------- 结构化输出

SCHEMA = {
    "type": "object",
    "properties": {"案由": {"type": "string"}, "金额": {"type": "number"}},
    "required": ["案由"],
}


def test_json_schema_uses_json_object_mode_and_injects_schema():
    """DeepSeek 没有严格 schema 模式，只能 json_object + 提示里带 schema。"""
    cap = {}
    m = _build(cap, content='{"案由": "建设工程施工合同纠纷", "金额": 120000}')
    r = m.chat([ChatMessage(role="user", content="抽取要素")], json_schema=SCHEMA)

    assert cap["body"]["response_format"] == {"type": "json_object"}
    system = cap["body"]["messages"][0]
    assert system["role"] == "system"
    assert "案由" in system["content"]          # schema 被注入
    assert r.data == {"案由": "建设工程施工合同纠纷", "金额": 120000}


def test_json_tolerates_markdown_fence():
    cap = {}
    m = _build(cap, content='```json\n{"案由": "劳动争议"}\n```')
    r = m.chat([ChatMessage(role="user", content="x")], json_schema=SCHEMA)
    assert r.data == {"案由": "劳动争议"}


def test_json_retries_once_then_raises():
    """两次都不是合法 JSON 就抛错 —— 绝不静默返回半成品。"""
    from adapters.model.deepseek import DeepSeekModel

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={
            "id": "1", "object": "chat.completion", "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [{"index": 0, "message": {"role": "assistant",
                                                 "content": "不是 JSON"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    m = DeepSeekModel(_settings(),
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="合法 JSON"):
        m.chat([ChatMessage(role="user", content="x")], json_schema=SCHEMA)
    assert calls["n"] == 2, "应当且只应当重试一次"


# ---------------------------------------------------------------- 无 key / 假实现

def test_missing_api_key_fails_with_actionable_message():
    from adapters.model.deepseek import DeepSeekModel

    with pytest.raises(RuntimeError, match="LFP_DEEPSEEK_API_KEY"):
        DeepSeekModel(Settings(deepseek_api_key=""))


def test_fake_backend_needs_no_key():
    """没有 key 也能本地开发和跑 CI。"""
    from adapters.model.deepseek import build_model_adapter

    m = build_model_adapter(Settings(model_backend="fake", deepseek_api_key=""))
    r = m.chat([ChatMessage(role="user", content="hi")], json_schema=SCHEMA)
    assert r.model == "fake"
    assert set(r.data) == {"案由", "金额"}


def test_adapter_satisfies_the_port():
    from adapters.model.deepseek import build_model_adapter
    from kernel.ports import ModelPort

    m = build_model_adapter(Settings(model_backend="fake"))
    assert isinstance(m, ModelPort)


def test_empty_content_is_warned_not_silent(caplog):
    """推理吃光 max_tokens 导致正文为空 —— 这是静默失败，必须留下告警。"""
    import logging

    cap = {}
    m = _build(cap, content="")
    with caplog.at_level(logging.WARNING, logger="adapters.model.deepseek"):
        r = m.chat([ChatMessage(role="user", content="hi")], max_tokens=100)
    assert r.text == ""
    assert any("正文为空" in rec.message for rec in caplog.records)
