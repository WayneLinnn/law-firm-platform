"""法律检索接入。

用假实现跑 —— 检索服务是独立进程，测试不该依赖它起没起。
真连的验证在端到端脚本里做。

重点验证四件事：
  1. **案件上下文注入** —— 这是平台相对于"一个搜索框"的全部价值
  2. **引用存稳定标识** —— 库重建后历史文书的引用不能失效
  3. **留档** —— trace_id 是 Step 13 引用留痕的锚点；花费按租户记账
  4. **长任务** —— 提交/轮询，服务重启丢任务时要能识别
"""
import pytest
from sqlalchemy.orm import Session

from kernel.container import container
from kernel.errors import DomainError
from kernel.ports.legal_qa import LegalQAPort
from kernel.ports.retrieval import RetrievalPort
from modules.case import service as case_svc
from modules.crm import service as crm
from modules.identity import access
from modules.identity import service as identity
from modules.retrieval import service


@pytest.fixture(autouse=True)
def fake_retrieval():
    """把检索 Port 换成假实现，并在结束后还原。"""
    from adapters.retrieval.fake import FakeLegalRag

    fake = FakeLegalRag()
    container.bind("retrieval", lambda: fake)
    container.bind("legal_qa", lambda: fake)
    yield fake
    from kernel.bootstrap import bind_adapters

    bind_adapters()


@pytest.fixture
def session():
    from kernel.db.session import get_engine

    try:
        conn = get_engine().connect()
    except Exception as exc:
        pytest.skip("数据库不可用：{0}".format(exc))
    tx = conn.begin()
    s = Session(bind=conn, expire_on_commit=False)
    try:
        yield s
    finally:
        s.close()
        tx.rollback()
        conn.close()


@pytest.fixture
def firm(session: Session):
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = identity.create_tenant(session, "r-" + sfx, "康桥")
    identity.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in case_svc.ensure_default_causes(session, t.id)}

    root = identity.create_org_unit(session, t.id, "康桥", "root-" + sfx, kind="firm")
    team = identity.create_org_unit(session, t.id, "王团队", "t-" + sfx,
                                    kind="team", parent_id=root.id)
    u = identity.create_user(session, t.id, "wang" + sfx, "王建国", "password123")
    identity.appoint(session, u.id, "senior_partner")
    identity.assign_membership(session, u.id, team.id, is_primary=True)
    access.assign_role(session, u.id, "committee")

    ours = crm.create_party(session, t.id, "宏远建设" + sfx, allow_similar=True)
    theirs = crm.create_party(session, t.id, "凯达置业" + sfx, allow_similar=True)

    case = case_svc.create_case(
        session, t.id, "AJ-" + sfx, "建设工程施工合同纠纷",
        lead_id=u.id, cause_id=causes["construction"].id, org_unit_id=team.id,
        summary="工程已完工并交付使用，未经竣工验收，发包人拖欠工程款 320 万元",
    )
    case_svc.add_party(session, t.id, case.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, t.id, case.id, theirs.id, "defendant")

    return dict(tenant=t, user=u, case=case, sfx=sfx,
                ours=ours, theirs=theirs)


# ================================================================ 契约


def test_adapter_satisfies_both_ports(fake_retrieval):
    """同一个检索服务同时实现两个 Port —— 拆开是为了计费/缓存策略不同。"""
    assert isinstance(fake_retrieval, RetrievalPort)
    assert isinstance(fake_retrieval, LegalQAPort)


def test_real_adapter_also_satisfies_ports():
    from adapters.retrieval.legal_rag_http import LegalRagHttp
    from kernel.config import Settings

    adapter = LegalRagHttp(Settings(legal_rag_url="http://127.0.0.1:8100"))
    assert isinstance(adapter, RetrievalPort)
    assert isinstance(adapter, LegalQAPort)


# ================================================================ 案件上下文 ★


def test_context_is_built_from_the_case(session, firm):
    """★ 案件上下文注入 —— 平台相对于"一个搜索框"的全部价值。"""
    ctx = service.build_case_context(session, firm["case"].id)
    assert ctx is not None
    assert ctx.cause_of_action == "建设工程施工合同纠纷"
    assert "未经竣工验收" in ctx.facts
    # 对方当事人来自 crm 主体库，不是案件表上的字符串
    assert "凯达置业" in ctx.opponent_claims


def test_context_is_passed_to_the_service(session, firm, fake_retrieval):
    service.search(session, firm["tenant"].id, firm["user"].id,
                   "完工未验收能否主张工程款", case_id=firm["case"].id)
    call = fake_retrieval.calls[-1]
    assert call["context"] is not None
    assert call["context"].cause_of_action == "建设工程施工合同纠纷"


def test_context_switch_only_exists_on_the_qa_path(session, firm, fake_retrieval):
    """★ 「带本案背景」开关只在问答路径上有效，纯检索没有这个开关。

    案件上下文是喂给改写节点的；/search 直接查 BM25+向量，不走改写。
    实测 A/B：纯检索带与不带，返回的引用一模一样 ——
    所以接口上根本不提供这个参数，免得界面出现一个骗人的开关。
    """
    import inspect

    assert "with_context" not in inspect.signature(service.search).parameters
    assert "with_context" in inspect.signature(service.submit).parameters

    # 问答路径上关掉，上下文确实不传
    service.submit(session, firm["tenant"].id, firm["user"].id,
                   "answer", "完工未验收", case_id=firm["case"].id,
                   with_context=False)
    assert fake_retrieval.calls[-1]["context"] is None


def test_no_case_means_no_context(session, firm, fake_retrieval):
    """不挂案件的独立检索照样能用。"""
    service.search(session, firm["tenant"].id, firm["user"].id, "民法典第807条")
    assert fake_retrieval.calls[-1]["context"] is None


# ================================================================ 稳定引用 ★


def test_citations_use_stable_ids_not_chunk_ids(session, firm):
    """★ 引用必须绑在稳定标识上。

    检索库会大改（换数据源、重新切分、扩充法条），chunk_id 会重排。
    引用如果绑在 chunk_id 上，库一重建，**所有历史文书的引用都会指向
    错误内容** —— 比没有引用还糟。
    """
    out = service.search(session, firm["tenant"].id, firm["user"].id,
                         "完工未验收", case_id=firm["case"].id)
    rows = service.citations_of(session, out["log"].id)
    assert rows

    first = rows[0]
    assert first.source_id == "法释〔2020〕25号#第十四条"
    assert not first.source_id.isdigit(), "source_id 不能是 chunk_id"
    # chunk_id 只放 locator，作"当时的定位"
    assert first.locator == "4321"
    # 原文片段，不是摘要 —— 引用校验与文书留痕都要看原文
    assert "擅自使用" in first.snippet


def test_effective_status_is_recorded(session, firm):
    """律师最怕引到已废止的条文。"""
    out = service.search(session, firm["tenant"].id, firm["user"].id, "完工未验收")
    rows = service.citations_of(session, out["log"].id)
    assert "现行有效" in rows[0].effective_status


def test_corpus_version_is_recorded(session, firm):
    """库重建后能回答"这条引用出自哪个版本"。"""
    out = service.search(session, firm["tenant"].id, firm["user"].id, "完工未验收")
    assert out["log"].corpus_version == "fake-v1"


# ================================================================ 留档


def test_trace_id_anchors_the_citation(session, firm):
    """trace_id 是 Step 13 引用留痕的锚点。"""
    out = service.search(session, firm["tenant"].id, firm["user"].id,
                         "完工未验收", case_id=firm["case"].id)
    assert out["log"].trace_id
    assert out["log"].case_id == firm["case"].id


def test_history_is_queryable_by_case(session, firm):
    """律师要能看"我上次在这个案子里查过什么"。"""
    for q in ("完工未验收", "实际施工人", "工程款利息"):
        service.search(session, firm["tenant"].id, firm["user"].id,
                       q, case_id=firm["case"].id)
    logs = service.logs_of_case(session, firm["case"].id)
    assert [x.question for x in logs] == ["工程款利息", "实际施工人", "完工未验收"]


def test_pure_search_costs_nothing(session, firm):
    out = service.search(session, firm["tenant"].id, firm["user"].id, "完工未验收")
    assert float(out["log"].cost_cny) == 0.0
    assert out["log"].kind == "search"


def test_cost_is_accumulated_per_tenant(session, firm):
    """按租户记账 —— 先只记账不限额，等真有滥用再加配额。"""
    service.search(session, firm["tenant"].id, firm["user"].id, "a")
    log = service.submit(session, firm["tenant"].id, firm["user"].id,
                         "answer", "b", case_id=firm["case"].id)
    service.poll(session, log.id)

    stats = service.cost_of_tenant(session, firm["tenant"].id)
    assert stats["count"] == 2


def test_empty_query_rejected(session, firm):
    with pytest.raises(DomainError, match="不能为空"):
        service.search(session, firm["tenant"].id, firm["user"].id, "   ")


# ================================================================ 长任务


def test_ask_is_async(session, firm):
    """问答实测 15-26 秒，不做同步等待。"""
    log = service.submit(session, firm["tenant"].id, firm["user"].id,
                         "answer", "完工未验收能否主张工程款", case_id=firm["case"].id)
    assert log.status == "queued"
    assert log.job_id
    assert log.answer == ""


def test_poll_lands_the_result(session, firm):
    log = service.submit(session, firm["tenant"].id, firm["user"].id,
                         "answer", "完工未验收", case_id=firm["case"].id)
    done = service.poll(session, log.id)

    assert done.status == "done"
    assert "工程款" in done.answer
    assert done.intent == "case_search"
    assert done.finished_at is not None

    rows = service.citations_of(session, done.id)
    # used 标记答复实际引用到的那几条 —— 文书留痕只需要这些
    assert [c.used for c in rows] == [True, False]


def test_extract_result_is_stored(session, firm):
    log = service.submit(session, firm["tenant"].id, firm["user"].id,
                         "extract", "工程已完工并交付使用…", case_id=firm["case"].id)
    done = service.poll(session, log.id)
    assert done.status == "done"
    assert "建设工程施工合同纠纷" in done.answer


def test_lost_job_is_marked_failed(session, firm, fake_retrieval):
    """★ 检索服务重启会丢任务（任务表在进程内存里）。

    这时轮询要把本地记录标记为失败，而不是一直转圈。
    """
    log = service.submit(session, firm["tenant"].id, firm["user"].id,
                         "answer", "完工未验收", case_id=firm["case"].id)
    fake_retrieval._jobs.clear()          # 模拟服务重启

    done = service.poll(session, log.id)
    assert done.status == "failed"
    assert "不存在" in done.error


def test_bad_kind_rejected(session, firm):
    with pytest.raises(DomainError, match="answer / extract / compare"):
        service.submit(session, firm["tenant"].id, firm["user"].id, "search", "x")


# ================================================================ 服务不可用


def test_health_reports_unavailable_instead_of_crashing(session):
    """界面要能区分"服务没起"和"真没结果"。"""
    from adapters.retrieval.legal_rag_http import LegalRagHttp
    from kernel.config import Settings

    container.bind("retrieval", lambda: LegalRagHttp(
        Settings(legal_rag_url="http://127.0.0.1:59999", legal_rag_timeout=1)))
    out = service.health(session)
    assert out["status"] == "unavailable"
    assert out["error"]
