# -*- coding: utf-8 -*-
"""内存版流程引擎。没有 Postgres 也能开发、跑单测。

**刻意不做持久化** —— 它的存在是为了让不关心流程的测试能跑，
不是为了当备胎。真要跨进程恢复，只有 Postgres 版算数。

它按 Template 的 Step 列表老实往下走，只是状态放内存：
这样业务侧的步骤逻辑（建工那套规则）用它也能测，不必连库。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from kernel.errors import DomainError, NotFound
from kernel.ports.workflow import Template, WorkflowRun


class FakeWorkflowEngine:
    """实现 kernel.ports.WorkflowPort，状态放进程内存。"""

    def __init__(self, templates: Optional[Dict[str, Template]] = None) -> None:
        self._templates: Dict[str, Template] = dict(templates or {})
        self._runs: Dict[str, Dict[str, Any]] = {}

    # ---------------------------------------------------------- 注册

    def register(self, template: Template) -> None:
        self._templates[template.key] = template

    def template(self, key: str) -> Template:
        tpl = self._templates.get(key)
        if tpl is None:
            raise NotFound("没有这个流程模板：{0}".format(key),
                           code="workflow.no_template")
        return tpl

    def templates(self) -> List[str]:
        return sorted(self._templates)

    def describe(self) -> List[Dict[str, Any]]:
        return [{"key": k, "label": self._templates[k].label,
                 "steps": [{"key": s.key, "title": s.title}
                           for s in self._templates[k].steps]}
                for k in sorted(self._templates)]

    # ---------------------------------------------------------- 跑

    def _advance(self, rec: Dict[str, Any]) -> WorkflowRun:
        """从当前步一直往下走，直到要问人或走完。"""
        tpl = self.template(rec["template_key"])
        state = rec["state"]
        while rec["i"] < len(tpl.steps):
            step = tpl.steps[rec["i"]]
            if step.key not in (state.get("trail") or []):
                if step.run is not None:
                    try:
                        state.update(step.run(state) or {})
                    except Exception as exc:
                        state["__error__"] = "{0}: {1}".format(type(exc).__name__, exc)
                        rec["status"] = "failed"
                        rec["node"] = step.key
                        return self._snap(rec)
            if step.ask is not None and not rec.get("asked"):
                q = step.ask(state) or ""
                if q:
                    rec.update({"status": "waiting_human", "node": step.key,
                                "question": q, "asked": True})
                    return self._snap(rec)
            state["trail"] = list(state.get("trail") or []) + [step.key]
            state["__last_node__"] = step.key
            rec["i"] += 1
            rec["asked"] = False
        rec.update({"status": "done", "node": tpl.steps[-1].key if tpl.steps else "",
                    "question": ""})
        return self._snap(rec)

    def _snap(self, rec: Dict[str, Any]) -> WorkflowRun:
        return WorkflowRun(
            run_id=rec["run_id"], template_key=rec["template_key"],
            case_id=rec["case_id"], status=rec["status"], current_node=rec["node"],
            state=dict(rec["state"]), pending_question=rec.get("question") or None,
        )

    def start(self, template_key: str, case_id: str, payload: Dict[str, Any],
              run_id: Optional[str] = None) -> WorkflowRun:
        tpl = self.template(template_key)
        missing = [f for f in tpl.requires if not str((payload or {}).get(f, "")).strip()]
        if missing:
            raise DomainError("发起 {0} 缺少必填项：{1}".format(
                tpl.label, "、".join(missing)), code="workflow.missing_payload")
        run_id = run_id or uuid.uuid4().hex
        state = dict(payload or {})
        state.setdefault("case_id", str(case_id))
        state.setdefault("trail", [])
        self._runs[run_id] = {"run_id": run_id, "template_key": template_key,
                              "case_id": str(case_id), "state": state, "i": 0,
                              "status": "running", "node": "", "question": "",
                              "asked": False}
        return self._advance(self._runs[run_id])

    def resume(self, run_id: str, human_input: Dict[str, Any]) -> WorkflowRun:
        rec = self._runs.get(run_id)
        if rec is None:
            raise NotFound("流程不存在：{0}".format(run_id), code="workflow.no_run")
        tpl = self.template(rec["template_key"])
        step = tpl.steps[rec["i"]]
        if step.apply is not None:
            rec["state"].update(step.apply(
                rec["state"], str(human_input.get("answer", "")),
                dict(human_input.get("data") or {})) or {})
        rec["state"]["trail"] = list(rec["state"].get("trail") or []) + [step.key]
        rec["state"]["__last_node__"] = step.key
        rec["i"] += 1
        rec["asked"] = False
        rec["status"] = "running"
        return self._advance(rec)

    def get(self, run_id: str) -> WorkflowRun:
        rec = self._runs.get(run_id)
        if rec is None:
            raise NotFound("流程不存在：{0}".format(run_id), code="workflow.no_run")
        return self._snap(rec)

    def state_of(self, template_key: str, run_id: str, case_id: str) -> WorkflowRun:
        return self.get(run_id)
