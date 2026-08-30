"""适配器自带的模板 —— 只有一张**自检用的玩具图**。

真正的业务流程（建工、劳动争议…）由 `modules/workflow` 声明并注册，
因为铁律是「adapters 不得 import modules」：业务知识不能落在适配器里。

这张玩具图留着的理由：它不含任何业务，专门用来验证底座本身
（能跑、能中断、**关掉进程后能接着跑**）。业务模块全拆掉时它还在，
底座坏没坏一测便知。
"""
from __future__ import annotations

from typing import Any, Dict

from kernel.ports.workflow import Step, Template


def _draft(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"summary": "草拟：{0}".format(state.get("facts") or "（无案情）")}


def _ask_confirm(state: Dict[str, Any]) -> str:
    return "请确认这段摘要是否可用：{0}".format(state.get("summary", ""))


def _apply_confirm(state: Dict[str, Any], answer: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"confirmed": answer}


def _archive(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"summary": "{0}（律师确认：{1}）".format(
        state.get("summary", ""), state.get("confirmed", ""))}


TOY = Template(
    key="toy.confirm",
    label="自检流程（拟稿 → 律师确认 → 归档）",
    steps=[
        Step(key="draft", title="拟稿", run=_draft),
        Step(key="confirm", title="律师确认", ask=_ask_confirm, apply=_apply_confirm),
        Step(key="archive", title="归档", run=_archive),
    ],
)

#: key -> Template。业务模板由 modules/workflow 在装载时 register 进来。
BUILTIN_TEMPLATES: Dict[str, Template] = {TOY.key: TOY}
