# -*- coding: utf-8 -*-
"""Step 10 · 建设工程流程。

**这一步的验收标准是"流程驱动检索比律师自己搜更有用"**，所以测试分两层：

  一、规则层（本文件大部分）—— 时限算得对不对、焦点挑得对不对。
      这层是纯函数，0 成本，必须扎实：算错时限比不算更危险。

  二、流程层 —— 八步能不能跑通、律师的修正能不能覆盖机器的抽取、
      律师的确认有没有留痕。

真·A/B（流程 vs 裸搜）在端到端脚本里做，那要连真检索服务。
"""
from __future__ import annotations

from datetime import date

import pytest

from kernel.config import settings
from kernel.container import container
from kernel.errors import DomainError
from modules.workflow import construction as cn


# ================================================================ 时限


def _el(**kw):
    return cn.normalize_elements(kw)


def test_priority_deadline_is_six_months_from_payable():
    """优先受偿权 6 个月，从「应付款之日」起算。**除斥期间，最要命的一条。**"""
    d = cn.compute_deadlines(_el(payable_on="2026-01-20"), cn.CONTRACTOR,
                             today=date(2026, 3, 1))
    p = [x for x in d if x["key"] == "priority"][0]
    assert p["deadline_on"] == "2026-07-20"
    assert p["status"] == "ok"
    assert p["fatal"] is True, "这是除斥期间，不是普通时效 —— 标错了律师会不当回事"


def test_expired_priority_says_the_right_is_gone():
    """过期不能只说"超期"，要说清**权利没了**。"""
    d = cn.compute_deadlines(_el(payable_on="2025-04-20"), cn.CONTRACTOR,
                             today=date(2026, 8, 26))
    p = [x for x in d if x["key"] == "priority"][0]
    assert p["status"] == "expired"
    assert "已丧失" in p["message"], "过期的后果必须写明，不能只报个天数"


def test_deadline_within_30_days_is_urgent():
    d = cn.compute_deadlines(_el(payable_on="2026-03-01"), cn.CONTRACTOR,
                             today=date(2026, 8, 20))
    p = [x for x in d if x["key"] == "priority"][0]
    assert p["status"] == "urgent" and p["days_left"] == 12   # 8/20 → 9/1


def test_missing_date_is_reported_not_skipped():
    """★ 算不出来必须**明说**。

    静默跳过 = 律师以为"系统没报警所以没问题" —— 这是最坏的一种错。
    """
    d = cn.compute_deadlines(_el(completed_on="2025-01-01"), cn.CONTRACTOR,
                             today=date(2026, 8, 26))
    p = [x for x in d if x["key"] == "priority"][0]
    assert p["status"] == "unknown"
    assert "应付款日期" in p["message"]


def test_fatal_and_expired_sort_first():
    """要命的排前面 —— 律师只看前两行也不能漏掉致命项。"""
    d = cn.compute_deadlines(_el(payable_on="2020-01-01", completed_on="2019-06-01"),
                             cn.CONTRACTOR, today=date(2026, 8, 26))
    assert d[0]["key"] == "priority" and d[0]["status"] == "expired"


def test_employer_does_not_get_contractor_deadlines():
    """发包人视角没有优先受偿权 —— 给错角色的提醒是噪音。"""
    d = cn.compute_deadlines(_el(payable_on="2026-01-20"), cn.EMPLOYER,
                             today=date(2026, 3, 1))
    assert not [x for x in d if x["key"] == "priority"]


def test_month_end_rollback():
    """8月31日 + 6个月 没有 2月31日，要退到 2月28/29。"""
    d = cn.compute_deadlines(_el(payable_on="2025-08-31"), cn.CONTRACTOR,
                             today=date(2025, 9, 1))
    p = [x for x in d if x["key"] == "priority"][0]
    assert p["deadline_on"] == "2026-02-28"


@pytest.mark.parametrize("raw,want", [
    ("2025年3月20日", date(2025, 3, 20)),
    ("2025-03-20", date(2025, 3, 20)),
    ("2025/3/20", date(2025, 3, 20)),
    ("竣工日期为2025.03.20，已交付", date(2025, 3, 20)),
    ("说不清", None),
    ("", None),
    ("2025-13-45", None),        # 非法日期宁可不认
])
def test_date_parsing_never_guesses(raw, want):
    """认不出来就返回 None。**绝不猜** —— 算错的时限律师会信，算不出的他会自己查。"""
    assert cn.parse_date(raw) == want


# ================================================================ 争议焦点


def test_issues_come_from_elements_not_from_guessing():
    """★ 这条是"流程驱动"的核心：焦点由要素决定。"""
    plain = cn.pick_issues(_el(unpaid_amount="1200000"), cn.CONTRACTOR)
    keys = {i["key"] for i in plain}
    assert "settlement" in keys and "priority" in keys
    assert "validity" not in keys, "没有资质问题就不该查合同效力，查了是噪音"

    borrowed = cn.pick_issues(
        _el(unpaid_amount="1200000", borrowed_qualification="是"), cn.CONTRACTOR)
    assert "validity" in {i["key"] for i in borrowed}, "借了资质就必须查合同效力"


def test_unaccepted_use_triggers_the_strongest_card():
    """未验收擅自使用 —— 承包人手里最硬的牌，不能漏。"""
    issues = cn.pick_issues(_el(used_without_acceptance="是"), cn.CONTRACTOR)
    hit = [i for i in issues if i["key"] == "acceptance"]
    assert hit, "未验收即使用没触发对应焦点"
    joined = " ".join(hit[0]["queries"])
    assert "擅自使用" in joined, "检索式里没有本案最关键的那个词"


def test_employer_has_no_priority_issue():
    issues = cn.pick_issues(_el(unpaid_amount="100"), cn.EMPLOYER)
    assert "priority" not in {i["key"] for i in issues}


def test_every_issue_carries_queries_and_reason():
    """每个焦点都要能回答"为什么查它"和"用什么查" —— 否则律师没法复核。"""
    issues = cn.pick_issues(
        _el(unpaid_amount="1", borrowed_qualification="是", tendered="是",
            used_without_acceptance="是", actual_builder="是", delayed="是"),
        cn.CONTRACTOR)
    assert len(issues) >= 6
    for i in issues:
        assert i["queries"], "{0} 没有检索式".format(i["key"])
        assert i["why"], "{0} 没写为什么查".format(i["key"])


# ================================================================ 要素规整


def test_boolean_elements_tolerate_model_sloppiness():
    """模型会把「是」写成各种花样，规整只做一次，规则那边就不用每条防一遍。"""
    for raw in ("是", "true", "1", "有", True):
        assert cn.normalize_elements({"used_without_acceptance": raw})[
            "used_without_acceptance"] is True
    for raw in ("否", "", "无", None, False):
        assert cn.normalize_elements({"used_without_acceptance": raw})[
            "used_without_acceptance"] is False


def test_digest_only_lists_what_we_have():
    text = cn.elements_digest(_el(project="某工程", unpaid_amount="120万",
                                  used_without_acceptance="是"))
    assert "某工程" in text and "未验收即使用：是" in text
    assert "借用资质" not in text, "没有的要素不该占地方"


# ================================================================ 流程


@pytest.fixture
def flow_engine(monkeypatch):
    """用假引擎 + 假模型 + 假检索跑完整八步。

    这里要测的是**步骤之间怎么衔接**（律师的修正有没有盖住机器的抽取、
    确认有没有留痕），不是模型答得好不好 —— 那个要真跑，在端到端脚本里。
    """
    from adapters.workflow.fake import FakeWorkflowEngine
    from kernel.ports.model import ModelResult
    from kernel.ports.retrieval import Citation, RetrievalResult
    from modules.workflow import service
    from modules.workflow.templates import TEMPLATES

    class FakeModel:
        def __init__(self):
            self.calls = []

        def chat(self, messages, **kw):
            self.calls.append((messages, kw))
            if kw.get("json_schema"):
                return ModelResult(text="", data={
                    "project": "恒和商务中心",
                    "unpaid_amount": "12400000",
                    "completed_on": "2025年3月20日",
                    "payable_on": "2025年4月20日",
                    "used_without_acceptance": "是",
                }, usage={"cost_usd": 0.001})
            return ModelResult(text="【结论】可以主张工程款【S1】。", usage={"cost_usd": 0.007})

    class FakeRetrieval:
        def __init__(self):
            self.queries = []

        def search(self, request):
            self.queries.append(request.query)
            return RetrievalResult(citations=[
                Citation(source_type="statute",
                         source_id="建工解释一_法释2020-25号#第十四条",
                         title="建工解释一", snippet="未经竣工验收擅自使用……",
                         effective_status="现行有效"),
            ])

    model, retrieval = FakeModel(), FakeRetrieval()
    engine = FakeWorkflowEngine()
    for tpl in TEMPLATES:
        engine.register(tpl)

    container.bind("workflow", lambda: engine)
    container.bind("model", lambda: model)
    container.bind("retrieval", lambda: retrieval)
    monkeypatch.setattr(service, "_REGISTERED", True, raising=False)
    was = settings.workflow_async
    settings.workflow_async = False
    yield engine, model, retrieval
    settings.workflow_async = was
    from kernel.bootstrap import bind_adapters
    bind_adapters()


def test_flow_stops_for_the_lawyer_before_spending_anything_big(flow_engine):
    """第一停点是要素确认 —— **先让律师把日期看一遍，再谈别的。**"""
    engine, model, _ = flow_engine
    run = engine.start("construction.dispute", "1", {"facts": "工程已完工未验收，欠款1240万"})
    assert run.status == "waiting_human"
    assert run.current_node == "confirm_elements"
    assert "应付款日期" in run.pending_question, "确认环节没提醒最关键的那个日期"


def test_lawyer_correction_overrides_the_model(flow_engine):
    """★ 人说了算：律师改的日期必须盖掉模型抽的。"""
    engine, _, _ = flow_engine
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    assert run.state["elements"]["payable_on"] == "2025年4月20日"

    run = engine.resume(run.run_id, {
        "answer": "应付款日期抽错了，以结算单为准",
        "data": {"payable_on": "2026-06-01"},
    })
    assert run.state["elements"]["payable_on"] == "2026-06-01"
    p = [d for d in run.state["deadlines"] if d["key"] == "priority"][0]
    assert p["anchor_on"] == "2026-06-01", "时限没按律师改后的日期重算"


def test_research_queries_come_from_the_issues(flow_engine):
    """★ 验收点：律师一个字都没敲，流程自己发出了十几条精确检索式。"""
    engine, _, retrieval = flow_engine
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})

    assert run.status == "waiting_human"
    assert run.current_node == "confirm_issues"
    assert len(retrieval.queries) >= 9, "检索式太少，流程驱动没兑现"
    joined = " ".join(retrieval.queries)
    assert "擅自使用" in joined and "优先受偿权" in joined
    assert not any(q.strip() == "工程款" for q in retrieval.queries), \
        "退化成裸搜「工程款」了 —— 那就是律师自己搜，流程没带来任何东西"

    # 每个焦点都带上了依据
    assert run.state["research"] and all(r["citations"] for r in run.state["research"])


def test_lawyer_can_drop_an_issue(flow_engine):
    engine, _, _ = flow_engine
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    before = {r["key"] for r in run.state["research"]}
    assert "interest" in before

    run = engine.resume(run.run_id, {"answer": "利息不在本次范围", "data": {"drop": ["interest"]}})
    assert "interest" not in {r["key"] for r in run.state["research"]}


def test_draft_only_gets_retrieved_citations(flow_engine):
    """★ 起草时只把检索到的依据喂进去，并明确禁止引用别的。

    这是"敢签字"的地基：模型编不出条号，因为它手里只有这些。
    """
    engine, model, _ = flow_engine
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "焦点无误", "data": {}})

    assert run.status == "waiting_human" and run.current_node == "signoff"
    draft_prompt = model.calls[-1][0][-1].content
    assert "建工解释一_法释2020-25号#第十四条" in draft_prompt
    assert "不得引用未列出的法条或案例" in draft_prompt
    assert model.calls[-1][1].get("thinking") is True, "起草要判断，得开思考"


def test_extraction_does_not_waste_thinking_tokens(flow_engine):
    """抽要素是机械活，开思考纯烧钱（llm.py 里实测白花 76%）。"""
    engine, model, _ = flow_engine
    engine.start("construction.dispute", "1", {"facts": "案情"})
    assert model.calls[0][1].get("thinking") is False


def test_flow_refuses_to_start_without_facts(flow_engine):
    """没案情就别起流程 —— 跑出来的东西是空的，反而误导。"""
    engine, _, _ = flow_engine
    with pytest.raises(DomainError):
        engine.start("construction.dispute", "1", {"facts": "   "})


def test_whole_flow_reaches_signoff(flow_engine):
    """八步走完，最后停在律师终审。"""
    engine, _, _ = flow_engine
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "焦点无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "已通读全文，确认可用", "data": {}})

    assert run.status == "done"
    assert run.state["final"], "终稿没落下来"
    assert run.state["signoff_note"] == "已通读全文，确认可用"
    assert run.state["trail"] == [
        "extract", "confirm_elements", "deadlines", "issues",
        "research", "confirm_issues", "draft", "signoff",
    ]


def test_extraction_failure_does_not_kill_the_flow(flow_engine):
    """模型挂了，律师手工填要素照样能往下走 —— 不能一个外部故障就废掉整条流程。"""
    engine, model, _ = flow_engine

    def boom(messages, **kw):
        raise RuntimeError("模型服务不可达")

    model.chat = boom
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    assert run.status == "waiting_human"
    assert "自动抽取失败" in run.pending_question


def test_empty_draft_is_reported_as_an_error(flow_engine):
    """★ 空正文必须报错，不能说成「已生成（0 字）」。

    回归：起草时开了思考，**推理 token 也占输出配额**。实测同一提示词
    max_tokens=4000 时推理把配额全吃光、正文返回空串，8000 才正常。
    当时流程把空串当成正常草稿，问律师「已生成（0 字），请确认」——
    律师会以为模型认为无话可说，而不是配额不够。
    """
    from kernel.ports.model import ModelResult

    engine, model, _ = flow_engine
    real = model.chat

    def empty_draft(messages, **kw):
        if kw.get("json_schema"):
            return real(messages, **kw)
        return ModelResult(text="", usage={"cost_usd": 0.02})

    model.chat = empty_draft
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "焦点无误", "data": {}})

    assert run.state.get("draft_error"), "空正文被当成正常草稿了"
    assert "配额" in run.state["draft_error"]
    assert "起草失败" in run.pending_question


def test_cost_is_accumulated_in_cny(flow_engine):
    """适配器按美元记账，平台按人民币展示 —— 换算率只有 settings 一处。"""
    engine, _, _ = flow_engine
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "焦点无误", "data": {}})
    assert run.state["cost_cny"] > 0, "花费没记上 —— usage 的键名对不上就会静默变成 0"


def test_empty_draft_falls_back_to_no_thinking(flow_engine):
    """★ 起草返回空正文时，关掉思考重试一次。

    开着思考时**推理 token 也吃输出配额**，而它吃多少不可预测：
    同样的提示词这次 8000 够、下次就不够（实测两次都撞上了）。
    靠调大配额只是把墙往后挪。起草本质是「把给定材料组织成文」，
    资料都在提示词里，不开思考照样写得出来 —— 质量略降，
    比交一份空白强得多。
    """
    from kernel.ports.model import ModelResult

    engine, model, _ = flow_engine
    real, calls = model.chat, []

    def flaky(messages, **kw):
        if kw.get("json_schema"):
            return real(messages, **kw)
        calls.append(kw.get("thinking"))
        if kw.get("thinking"):                 # 开思考那次被推理吃光
            return ModelResult(text="", usage={"cost_usd": 0.03})
        return ModelResult(text="【结论】可以主张工程款【S1】。", usage={"cost_usd": 0.01})

    model.chat = flaky
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "焦点无误", "data": {}})

    assert calls == [True, False], "没有降级重试：{0}".format(calls)
    assert run.state["draft"], "降级之后仍然没拿到正文"
    assert not run.state.get("draft_error")
    assert "关闭思考" in run.state.get("draft_note", "")
    assert "已关闭思考模式" in run.pending_question, "得让律师知道这稿是降级生成的"


def test_nothing_to_archive_says_so(flow_engine):
    """★ 草稿为空时不能静默跳过归档。

    静默返回等于让律师以为「存好了」—— 这正是本项目一再吃亏的那类错：
    空结果不会引起怀疑。
    """
    from kernel.ports.model import ModelResult

    engine, model, _ = flow_engine
    real = model.chat

    def always_empty(messages, **kw):
        if kw.get("json_schema"):
            return real(messages, **kw)
        return ModelResult(text="", usage={"cost_usd": 0.02})

    model.chat = always_empty
    run = engine.start("construction.dispute", "1", {"facts": "案情"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "焦点无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "知悉，先这样", "data": {}})

    assert run.state.get("archive_note"), "没归档却什么都不说"
    assert "草稿为空" in run.state["archive_note"]


def test_archive_note_records_which_flow_produced_it(flow_engine):
    """归档的文书要能追回是哪条流程产出的 —— Step 13 引用留痕靠这类链接。"""
    engine, _, _ = flow_engine
    run = engine.start("construction.dispute", "1",
                       {"facts": "案情", "run_id": "abc123def456"})
    run = engine.resume(run.run_id, {"answer": "要素无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "焦点无误", "data": {}})
    run = engine.resume(run.run_id, {"answer": "同意出具", "data": {}})
    # 没装 doc 模块时也要说清楚为什么没归档
    assert run.state.get("archive_note"), "归档结果一个字都没说"
