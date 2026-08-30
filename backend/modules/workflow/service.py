# -*- coding: utf-8 -*-
"""流程编排的业务层。

职责边界（想清楚再改）：

    引擎（adapters/workflow）  只管"图怎么跑、状态存哪"
    本模块                     只管"谁能跑、挂在哪个案子、流转怎么留痕"

引擎不知道租户、权限、案件是什么；本模块不知道 langgraph 是什么。
两边只通过 `WorkflowPort` 和 `Template`（Step 列表）说话。

**为什么业务记录和 checkpoint 要各存一份**：
checkpoint 是引擎私有结构、随库版本变；而"这条流程属于哪个案子、
谁发起的、律师什么时候确认的"是要给合规看的业务事实，
必须由我们按稳定结构自己记。两者靠 run_id 对齐。

**为什么推进要放后台线程**（Step 10 加的）：
建工流程一趟要跑抽要素（模型）+ 十几次检索 + 起草（模型），
实测几十秒。同步 HTTP 会把请求吊死 —— 和 Step 8 给检索做任务化
是同一个理由。所以 start/resume 立刻返回 `running`，客户端轮询。
`LFP_WORKFLOW_ASYNC=false` 可以切回同步，单测走这条路。
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.config import settings
from kernel.container import container
from kernel.db.session import session_scope
from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from kernel.security import Principal
from modules.workflow.models import RUN_STATUSES, WorkflowRun, WorkflowStep

logger = logging.getLogger(__name__)

#: 这些状态下流程已经结束，不能再 resume
FINAL_STATUSES = ("done", "failed", "cancelled")

#: 同时最多推进几条流程。模型调用和检索都吃资源，别放太开。
_SLOTS = threading.Semaphore(4)
_REGISTERED = False
_REG_LOCK = threading.Lock()


def _engine():
    """拿引擎，并保证业务模板已经注册进去。

    ★ 注册放在这里而不是 bootstrap：铁律「adapters 不得 import modules」，
      所以适配器不可能自己去认识建工流程，只能由业务侧推给它。
    """
    engine = container.get("workflow")
    global _REGISTERED
    if not _REGISTERED:
        with _REG_LOCK:
            if not _REGISTERED:
                from modules.workflow.templates import TEMPLATES

                for tpl in TEMPLATES:
                    engine.register(tpl)
                _REGISTERED = True
                logger.info("已注册业务流程模板：%s",
                            "、".join(t.key for t in TEMPLATES))
    return engine


def available_templates() -> List[str]:
    return list(_engine().templates())


def describe_templates() -> List[Dict[str, Any]]:
    engine = _engine()
    if hasattr(engine, "describe"):
        return engine.describe()
    return [{"key": k, "label": k, "steps": []} for k in engine.templates()]


# ---------------------------------------------------------------- 发起


def _payload_for(session: Session, principal: Principal, case_id: int,
                 payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把案件里已有的东西喂进流程，别让律师再抄一遍。

    ★ 案情默认取案件的 summary —— 平台已经有的信息不该二次录入，
      这是"接进流程"相对于"另开一个工具"的基本盘。
    """
    out = dict(payload or {})
    case_api = registry.api("case")
    case = case_api.get_case(session, case_id)
    out.setdefault("facts", (getattr(case, "summary", "") or "").strip())
    out.setdefault("case_title", getattr(case, "title", ""))
    out.setdefault("case_code", getattr(case, "code", ""))
    out.setdefault("role", "contractor")
    # ★ 身份要随 state 走：流程在后台线程里跑，没有请求上下文，
    #   而归档、写文档这些动作都要过权限。
    out.setdefault("actor_id", principal.user_id)
    return out


def start(
    session: Session,
    principal: Principal,
    template_key: str,
    case_id: int,
    payload: Optional[Dict[str, Any]] = None,
) -> WorkflowRun:
    """为某个案件发起一条流程。

    案件可见性由 case 模块判定 —— 我们不去摸它的表（边界检查会拦）。
    """
    registry.api("case").assert_visible(session, principal, case_id)
    engine = _engine()
    if template_key not in engine.templates():
        raise NotFound("没有这个流程模板：{0}".format(template_key),
                       code="workflow.no_template")

    run_id = uuid.uuid4().hex
    full = _payload_for(session, principal, case_id, payload)
    # ★ 流程号要进 state：归档出来的文书得能追回「是哪条流程产出的」。
    #   Step 13 的引用留痕就靠这类链接。
    full["run_id"] = run_id
    row = WorkflowRun(
        run_id=run_id,
        tenant_id=principal.tenant_id,
        case_id=case_id,
        template_key=template_key,
        status="running",
        current_node=_first_step(engine, template_key),
        started_by=principal.user_id,
    )
    session.add(row)
    session.flush()
    _log(session, run_id, "start", "entered", actor_id=principal.user_id)

    if settings.workflow_async:
        # ★ 必须先提交：后台线程用的是另一条连接，看不到未提交的行
        session.commit()
        _spawn(lambda: engine.start(template_key, str(case_id), full, run_id=run_id),
               run_id, principal.user_id)
    else:
        run = engine.start(template_key, str(case_id), full, run_id=run_id)
        _sync(session, row, run, actor_id=principal.user_id)
    return row


def _first_step(engine, template_key: str) -> str:
    try:
        for t in engine.describe():
            if t["key"] == template_key and t["steps"]:
                return t["steps"][0]["key"]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- 恢复


def resume(
    session: Session,
    principal: Principal,
    run_id: str,
    answer: str,
    data: Optional[Dict[str, Any]] = None,
) -> WorkflowRun:
    """律师回答人工确认点，流程继续。

    ★ 责任落点就在这里 —— `answer` 是律师本人写下的确认，会进 checkpoint、
      也会进 workflow_step 留痕。所以必须要求 workflow.resume 权限，
      且要记 actor_id：事后追责问的是"谁点的确认"。

    `data` 是结构化修正（改抽错的日期、去掉不要的争议焦点）。
    人说了算 —— 机器抽出来的东西一律可以被覆盖。
    """
    row = get_run(session, principal, run_id)
    if row.status in FINAL_STATUSES:
        raise DomainError(
            "流程已经结束（{0}），不能再继续".format(row.status),
            code="workflow.already_finished",
        )
    if row.status != "waiting_human":
        raise DomainError(
            "流程当前不在人工确认点（{0}）".format(row.status),
            code="workflow.not_waiting",
        )
    if not answer.strip():
        raise DomainError("确认内容不能为空", code="workflow.empty_answer")

    _log(session, run_id, row.current_node, "answered",
         note=answer.strip(), actor_id=principal.user_id)

    engine = _engine()
    payload = dict(data or {})
    # ★ 谁点的确认，由服务端说了算 —— 不能让客户端自己报身份
    payload["__actor_id__"] = principal.user_id
    human = {
        "answer": answer.strip(),
        "data": payload,
        "__template__": row.template_key,
        "__case_id__": str(row.case_id),
    }
    if settings.workflow_async:
        row.status = "running"
        row.pending_question = ""
        session.commit()
        _spawn(lambda: engine.resume(run_id, human), run_id, principal.user_id)
    else:
        _sync(session, row, engine.resume(run_id, human), actor_id=principal.user_id)
    return row


def cancel(session: Session, principal: Principal, run_id: str, note: str = "") -> WorkflowRun:
    """中止流程。

    只改业务记录，**不动 checkpoint** —— 留着，将来要复盘"当时跑到哪"还查得到。
    """
    row = get_run(session, principal, run_id)
    if row.status in FINAL_STATUSES:
        raise DomainError("流程已经结束，不能再中止", code="workflow.already_finished")
    row.status = "cancelled"
    row.finished_at = datetime.now(timezone.utc)
    _log(session, run_id, row.current_node, "cancelled",
         note=note, actor_id=principal.user_id)
    session.flush()
    return row


# ---------------------------------------------------------------- 后台推进


def _spawn(work, run_id: str, actor_id: Optional[int]) -> None:
    """把一次推进丢到后台。**线程里必须自己开 session** —— 请求那条已经关了。"""

    def worker() -> None:
        with _SLOTS:
            run, err = None, ""
            try:
                run = work()
            except Exception as exc:          # 流程炸了不能让线程静默死掉
                logger.exception("流程 %s 推进失败", run_id)
                err = "{0}: {1}".format(type(exc).__name__, exc)
            try:
                with session_scope() as s:
                    row = s.scalar(select(WorkflowRun).where(WorkflowRun.run_id == run_id))
                    if row is None:
                        logger.warning("流程 %s 的业务记录不见了，结果无处可写", run_id)
                        return
                    if run is None:
                        row.status = "failed"
                        row.error = err
                        row.finished_at = datetime.now(timezone.utc)
                        _log(s, run_id, row.current_node, "failed", note=err)
                    else:
                        _sync(s, row, run, actor_id=actor_id)
            except Exception:
                logger.exception("流程 %s 结果回写失败", run_id)

    threading.Thread(target=worker, name="wf-" + run_id[:8], daemon=True).start()


# ---------------------------------------------------------------- 查询


def get_run(session: Session, principal: Principal, run_id: str) -> WorkflowRun:
    row = session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.run_id == run_id,
            WorkflowRun.tenant_id == principal.tenant_id,
        )
    )
    if row is None:
        raise NotFound("流程不存在：{0}".format(run_id), code="workflow.no_run")
    # 流程内容 = 案件内容，必须过案件的数据范围与隔离墙
    registry.api("case").assert_visible(session, principal, row.case_id)
    return row


def runs_of_case(session: Session, principal: Principal, case_id: int) -> List[WorkflowRun]:
    registry.api("case").assert_visible(session, principal, case_id)
    return list(session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.tenant_id == principal.tenant_id,
               WorkflowRun.case_id == case_id)
        .order_by(WorkflowRun.id.desc())
    ).all())


def steps_of(session: Session, run_id: str) -> List[WorkflowStep]:
    return list(session.scalars(
        select(WorkflowStep)
        .where(WorkflowStep.run_id == run_id)
        .order_by(WorkflowStep.id)
    ).all())


def live_state(session: Session, principal: Principal, run_id: str) -> Dict[str, Any]:
    """从引擎读当前 state。

    ★ 每次都问引擎，不缓存在业务表里 —— state 是引擎的真相，
      业务表里那份只是给列表页用的摘要，两者一旦分歧以引擎为准。
    """
    row = get_run(session, principal, run_id)
    engine = _engine()
    if not hasattr(engine, "state_of"):
        return {}
    try:
        run = engine.state_of(row.template_key, run_id, str(row.case_id))
    except Exception:
        logger.exception("读流程 %s 的状态失败", run_id)
        return {}
    # ★ 只在流程"停着"的时候对齐。
    #   running 意味着后台线程正拿着它，状态归线程管 ——
    #   这边再写一次会和线程打架（读到半路的快照就把行改错）。
    if row.status not in ("running", "cancelled"):
        _sync(session, row, run)
    return dict(run.state or {})


# ---------------------------------------------------------------- 内部


def _sync(session: Session, row: WorkflowRun, run, actor_id: Optional[int] = None) -> None:
    """把引擎的真相同步回业务记录。"""
    before = row.status
    row.status = run.status if run.status in RUN_STATUSES else row.status
    row.current_node = run.current_node or row.current_node
    row.pending_question = run.pending_question or ""
    if run.status in ("done", "failed") and row.finished_at is None:
        row.finished_at = datetime.now(timezone.utc)
    if run.status == "failed":
        row.error = str((run.state or {}).get("__error__", "")) or row.error

    # 状态真的变了才记一笔，别把留痕表写成噪音
    if before != row.status:
        event = {"waiting_human": "asked", "done": "finished",
                 "failed": "failed"}.get(row.status, "entered")
        _log(session, row.run_id, row.current_node, event,
             note=row.pending_question if row.status == "waiting_human" else row.error,
             actor_id=actor_id)
    session.flush()


def _log(
    session: Session,
    run_id: str,
    node: str,
    event: str,
    note: str = "",
    actor_id: Optional[int] = None,
) -> None:
    session.add(WorkflowStep(
        run_id=run_id, node=node or "", event=event,
        note=(note or "")[:4000], actor_id=actor_id,
    ))
    session.flush()


# ================================================================ 归档


def archive_inventory(session, principal, case_id):
    """卷宗目录里的办案流程记录。

    ★ 流程产出的意见书已经落进 doc 了，这里记的是**过程** ——
      跑过几次、每次走到哪一步、律师在哪几步确认了什么。
      「我们当时是怎么一步步得出这个结论的」，这在事后是要紧的。
    """
    registry.api("case").assert_visible(session, principal, case_id)
    rows = runs_of_case(session, principal, case_id)
    if not rows:
        return []
    done = len([r for r in rows if r.status == "done"])
    return [{"name": "办案流程记录", "count": len(rows),
             "note": "其中 {0} 条走完".format(done) if done != len(rows) else ""}]
