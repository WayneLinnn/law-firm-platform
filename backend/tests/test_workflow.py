# -*- coding: utf-8 -*-
"""Step 9 验收：流程底座能跑、能中断、**关掉进程后能恢复**。

最后那条是这一步真正的意义。一个建设工程案子的流程可能跑两个月，
中间要换人接力、要等鉴定 —— 内存版一重启就全丢了，等于没做。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from kernel.config import settings
from kernel.container import container
from kernel.errors import DomainError, NotFound
from kernel.ports.workflow import WorkflowPort
from modules.case import service as case_service
from modules.crm import service as crm_service
from modules.identity import access, service as identity_service
from modules.workflow import service

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def session():
    """★ 注意：checkpoint 走的是引擎**自己的连接池（autocommit）**，
    不在本 fixture 的事务里，所以不会被 rollback 带走。
    这正是跨进程恢复测试能成立的原因 —— 也意味着 checkpoint 表里
    会留下测试残渣，是可接受的代价（清理见 db purge workflow）。
    """
    from sqlalchemy.orm import Session as SASession

    from kernel.db.session import get_engine

    try:
        conn = get_engine().connect()
    except Exception as exc:
        pytest.skip("数据库不可用：{0}".format(exc))
    tx = conn.begin()
    s = SASession(bind=conn, expire_on_commit=False)
    try:
        yield s
    finally:
        s.close()
        tx.rollback()
        conn.close()


@pytest.fixture(autouse=True)
def real_engine():
    """用**真的** LangGraph 引擎，不用 fake。

    Step 9 的验收点就是"跨进程恢复"，fake 是内存版，用它等于没测。
    """
    from kernel.bootstrap import bind_adapters

    bind_adapters()
    if settings.workflow_backend != "langgraph":
        pytest.skip("workflow_backend 不是 langgraph，跳过底座验收")
    try:
        container.get("workflow")._checkpointer()
    except Exception as exc:
        pytest.skip("checkpointer 不可用（数据库没起？）：{0}".format(exc))
    # ★ 单测走同步推进：本文件的 session 活在一个最后会回滚的事务里，
    #   后台线程用的是另一条连接，看不见这些行。异步那条路由端到端脚本覆盖。
    was = settings.workflow_async
    settings.workflow_async = False
    yield
    settings.workflow_async = was
    # ★ 把容器还原。bind_adapters() 绑的是**全局**容器，
    #   本文件跑完之后那些绑定还留着，泄漏给后面所有测试 ——
    #   一次 case 模块加校验就把这条隐患炸了出来（300+ 条 error）。
    #   **autouse fixture 动了全局状态，就得在 teardown 里放回去。**
    container.reset()


@pytest.fixture
def firm(session):
    """最小可用的所：一个人、一个客户、一个案件。"""
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = identity_service.create_tenant(session, "wf-" + sfx, "康桥")
    identity_service.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in case_service.ensure_default_causes(session, t.id)}

    root = identity_service.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    team = identity_service.create_org_unit(session, t.id, "王团队", "t-" + sfx,
                                            kind="team", parent_id=root.id)
    user = identity_service.create_user(session, t.id, "wang" + sfx, "王建国",
                                        "password123", is_superuser=True)
    identity_service.appoint(session, user.id, "senior_partner", valid_from=date(2020, 1, 1))
    identity_service.assign_membership(session, user.id, team.id, org_role="负责人",
                                       is_primary=True, valid_from=date(2020, 1, 1))

    party = crm_service.create_party(session, t.id, "宏远建设集团有限公司",
                                     allow_similar=True)
    case = case_service.create_case(
        session, t.id, "AJ" + sfx[:6], "宏远诉凯达",
        lead_id=user.id, cause_id=causes["construction"].id, org_unit_id=team.id,
    )
    case_service.add_party(session, t.id, case.id, party.id, role="plaintiff", is_ours=True)

    principal = access.build_principal(session, user.id)
    return {"tenant": t, "user": user, "case": case, "principal": principal}


# ================================================================ 契约


def test_engine_satisfies_the_port():
    """引擎必须真的实现 WorkflowPort —— 契约不是注释，是可执行的。"""
    engine = container.get("workflow")
    assert isinstance(engine, WorkflowPort)


def test_upper_layers_never_import_langgraph():
    """★ 铁律：只有 adapters/workflow 能 import langgraph。

    这条如果破了，将来换编排引擎就得改业务代码 —— 整个 Ports 架构白做。
    """
    offenders = []
    for path in list((BACKEND / "modules").rglob("*.py")) + \
                list((BACKEND / "kernel").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "langgraph" in text and "import" in text:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import langgraph", "from langgraph")):
                    offenders.append("{0}: {1}".format(path.name, stripped))
    assert not offenders, "这些文件越界 import 了 langgraph：{0}".format(offenders)


# ================================================================ 跑起来


def test_toy_flow_stops_at_human_confirm(session, firm):
    """三节点流程会停在人工确认点，并把问题抛出来。"""
    run = service.start(session, firm["principal"], "toy.confirm",
                        firm["case"].id, {"facts": "工程已完工未验收"})

    assert run.status == "waiting_human", "应当挂起等人，而不是一路跑完"
    assert run.current_node == "confirm"
    assert "工程已完工未验收" in run.pending_question, \
        "抛给律师的问题里要带上下文，否则他不知道在确认什么"


def test_resume_finishes_the_flow(session, firm):
    """律师确认后跑完，且**是接着跑而不是从头跑**。"""
    run = service.start(session, firm["principal"], "toy.confirm",
                        firm["case"].id, {"facts": "工程已完工未验收"})
    run = service.resume(session, firm["principal"], run.run_id, "确认可用")

    assert run.status == "done"
    state = service.live_state(session, firm["principal"], run.run_id)
    assert state["confirmed"] == "确认可用"
    # trail 是"走过哪些节点"。从头跑的话 draft 会出现两次
    assert state["trail"] == ["draft", "confirm", "archive"], \
        "恢复应当接着 confirm 往下走，不能重跑 draft"
    assert "确认可用" in state["summary"]


def test_answer_is_recorded_with_who_said_it(session, firm):
    """★ 责任落点：谁点的确认、写了什么，必须留痕。"""
    run = service.start(session, firm["principal"], "toy.confirm",
                        firm["case"].id, {"facts": "案情"})
    service.resume(session, firm["principal"], run.run_id, "我已核对，可用")

    steps = service.steps_of(session, run.run_id)
    answered = [s for s in steps if s.event == "answered"]
    assert len(answered) == 1
    assert answered[0].note == "我已核对，可用"
    assert answered[0].actor_id == firm["user"].id, "没记是谁点的，追责就断了"


def test_empty_confirmation_is_refused(session, firm):
    """确认不能是空的 —— 空白确认等于没确认。"""
    run = service.start(session, firm["principal"], "toy.confirm",
                        firm["case"].id, {"facts": "案情"})
    with pytest.raises(DomainError):
        service.resume(session, firm["principal"], run.run_id, "   ")


def test_cannot_resume_a_finished_flow(session, firm):
    run = service.start(session, firm["principal"], "toy.confirm",
                        firm["case"].id, {"facts": "案情"})
    service.resume(session, firm["principal"], run.run_id, "确认")
    with pytest.raises(DomainError):
        service.resume(session, firm["principal"], run.run_id, "再确认一次")


def test_unknown_template_is_rejected(session, firm):
    with pytest.raises(NotFound):
        service.start(session, firm["principal"], "不存在的模板",
                      firm["case"].id, {})


def test_cancel_keeps_the_checkpoint(session, firm):
    """中止只改业务记录，checkpoint 留着供复盘。"""
    run = service.start(session, firm["principal"], "toy.confirm",
                        firm["case"].id, {"facts": "案情"})
    run = service.cancel(session, firm["principal"], run.run_id, "客户撤回委托")
    assert run.status == "cancelled"

    # 引擎里的状态还在
    state = service.live_state(session, firm["principal"], run.run_id)
    assert state.get("trail") == ["draft"], "checkpoint 不该被中止操作抹掉"


# ================================================================ 生死线 ★


def test_flow_survives_process_restart(session, firm):
    """★★ Step 9 的验收点：**关掉进程后还能接着跑。**

    在本进程里发起流程、让它停在人工确认点，然后**开一个全新的 python
    进程**去恢复它。新进程对这条流程一无所知，只有 run_id 和 Postgres 里
    的 checkpoint —— 跑通说明状态真的落盘了，而不是活在内存里。

    这正是律所的真实场景：今天张三起了流程，下周李四接手，
    中间服务器还重启过两次。
    """
    run = service.start(session, firm["principal"], "toy.confirm",
                        firm["case"].id, {"facts": "跨进程验证"})
    assert run.status == "waiting_human"
    run_id = run.run_id
    # ★ 这里**不需要** session.commit()：新进程只认 checkpoint，
    #   而 checkpoint 是引擎用自己的 autocommit 连接写的，早已落库。
    #   业务表那行反而会被 fixture 回滚掉 —— 所以下面直接调引擎，不走 service。

    script = (
        "import sys; sys.path.insert(0, r'{backend}')\n"
        "from kernel.bootstrap import bootstrap\n"
        "bootstrap()\n"
        "from kernel.container import container\n"
        "engine = container.get('workflow')\n"
        "run = engine.resume('{run_id}', {{'answer': '另一个进程确认的',\n"
        "                                  '__template__': 'toy.confirm',\n"
        "                                  '__case_id__': '{case_id}'}})\n"
        "print('STATUS=' + run.status)\n"
        "print('TRAIL=' + ','.join(run.state.get('trail', [])))\n"
        "print('CONFIRMED=' + str(run.state.get('confirmed')))\n"
    ).format(backend=BACKEND, run_id=run_id, case_id=firm["case"].id)

    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        cwd=str(BACKEND),
    )
    assert proc.returncode == 0, "新进程恢复失败：\n{0}\n{1}".format(
        proc.stdout, proc.stderr[-2000:])

    out = proc.stdout
    assert "STATUS=done" in out, out
    assert "TRAIL=draft,confirm,archive" in out, \
        "新进程应当接着 confirm 往下走，不是从 draft 重跑：\n" + out
    assert "CONFIRMED=另一个进程确认的" in out, out


def test_checkpoint_tables_live_in_workflow_schema(session, firm):
    """★ checkpoint 表必须落在 workflow schema 里。

    掉进 public 的话，「每个模块独占一个 schema」这条铁律就破了，
    `db purge workflow` 也清不干净 —— 可拆卸性是靠 schema 边界保证的。
    """
    from sqlalchemy import text

    service.start(session, firm["principal"], "toy.confirm", firm["case"].id, {})

    rows = session.execute(text(
        "select table_schema from information_schema.tables "
        "where table_name = 'checkpoints'"
    )).all()
    schemas = {r[0] for r in rows}
    assert "workflow" in schemas, "checkpoint 表没建在 workflow schema 里：{0}".format(schemas)
    assert "public" not in schemas, "checkpoint 表掉进 public 了，schema 边界破了"
