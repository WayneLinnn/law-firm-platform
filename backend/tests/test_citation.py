# -*- coding: utf-8 -*-
"""Step 13 · 引用留痕。

验收标准是一句话：**点文书任一句，看到出处、原文片段、时效状态。**

所以测四件事：

  1. 断句带得住偏移量 —— 否则「第几个字符」映射不回「第几句」
  2. **锚点不漂** —— 律师改了正文，位置照样对
  3. 快照存得全 —— 半年后检索库重建了，还原得出「当时引的是什么」
  4. 没有依据的句子要**标出来** —— 律师签字前要找的正是这些
"""
from __future__ import annotations

import json

import pytest

from kernel.container import container
from kernel.errors import DomainError, NotFound
from kernel.ports.model import ModelResult
from modules.case import service as case_svc
from modules.citation import service as cite
from modules.crm import service as crm
from modules.draft import service as draft
from modules.identity import access
from modules.identity import service as identity


@pytest.fixture
def session():
    from sqlalchemy.orm import Session as SASession

    from kernel.db.session import get_engine

    try:
        conn = get_engine().connect()
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("数据库不可用：{0}".format(exc))
    tx = conn.begin()
    s = SASession(bind=conn, expire_on_commit=False)
    try:
        yield s
    finally:
        s.close()
        tx.rollback()
        conn.close()


class FakeModel:
    def __init__(self):
        self.text = "被告擅自使用未经验收的工程，质量抗辩不成立【S1】。"

    def chat(self, messages, **kw):
        return ModelResult(text=self.text, usage={"cost_usd": 0.003})


@pytest.fixture(autouse=True)
def fakes():
    from adapters.storage.memory import MemoryStorage

    model = FakeModel()
    container.bind("model", lambda: model)
    container.bind("storage", lambda: MemoryStorage())
    yield model
    from kernel.bootstrap import bind_adapters

    bind_adapters()


@pytest.fixture
def firm(session):
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = identity.create_tenant(session, "ct-" + sfx, "康桥")
    identity.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in case_svc.ensure_default_causes(session, t.id)}
    root = identity.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    t1 = identity.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                  kind="team", parent_id=root.id)
    u = identity.create_user(session, t.id, "wang" + sfx, "王建国", "password123")
    identity.appoint(session, u.id, "senior_partner")
    identity.assign_membership(session, u.id, t1.id, is_primary=True)
    access.assign_role(session, u.id, "committee")

    ours = crm.create_party(session, t.id, "宏远建设集团有限公司", allow_similar=True)
    theirs = crm.create_party(session, t.id, "凯达置业有限公司", allow_similar=True)
    case = case_svc.create_case(
        session, t.id, "AJ" + sfx[:6], "宏远诉凯达", lead_id=u.id,
        cause_id=causes["construction"].id, org_unit_id=t1.id,
        court="青岛市中级人民法院", summary="工程未验收即使用，欠付1240万元。")
    case_svc.add_party(session, t.id, case.id, ours.id, role="plaintiff", is_ours=True)
    case_svc.add_party(session, t.id, case.id, theirs.id, role="defendant", is_ours=False)
    return {"tenant": t, "case": case,
            "p": access.build_principal(session, u.id)}


SOURCES = [
    {"source_type": "statute", "source_id": "建工解释一_法释2020-25号#第十四条",
     "title": "建工解释一", "snippet": "建设工程未经竣工验收，发包人擅自使用后……",
     "effective_status": "现行有效（2021-01-01 施行）",
     "corpus_version": "1030d8027c-802b542b", "trace_id": "abc123trace",
     "issue": "未经验收擅自使用的后果"},
    {"source_type": "statute", "source_id": "民法典_合同编#第八百零七条",
     "title": "民法典", "snippet": "建设工程的价款就该工程折价或者拍卖的价款优先受偿。",
     "effective_status": "现行有效", "corpus_version": "1030d8027c-802b542b",
     "trace_id": "abc123trace", "issue": "工程价款优先受偿权"},
    {"source_type": "case", "source_id": "合同法#第二百八十六条",
     "title": "合同法", "snippet": "（旧法）", "effective_status": "已失效",
     "corpus_version": "old", "trace_id": "old999", "issue": "旧法"},
]


def _mk(session, firm):
    row = draft.create(session, firm["p"], firm["case"].id, "civil_complaint")
    cite.attach(session, firm["p"], "draft", row.id, SOURCES)
    row.citations = json.dumps(SOURCES, ensure_ascii=False)
    session.flush()
    return row


# ================================================================ 断句


def test_sentences_carry_offsets():
    """★ 断句必须带偏移量 —— 否则没法把字符位置映射回句子。"""
    text = "工程已完工。被告擅自使用【S1】。原告有权主张工程款；诉讼费由被告承担。"
    sents = cite.split_sentences(text)
    assert len(sents) == 4
    for start, end, seg in sents:
        assert text[start:end] == seg, "偏移量对不上原文"


def test_sentence_split_handles_newlines_and_tail():
    sents = cite.split_sentences("第一句。\n第二句\n没有句号的结尾")
    assert len(sents) == 3
    assert sents[-1][2].strip() == "没有句号的结尾"


def test_empty_text_yields_no_sentences():
    assert cite.split_sentences("") == []
    assert cite.split_sentences("   \n  ") == []


# ================================================================ 锚点


def test_anchor_points_at_the_right_sentence(session, firm):
    row = _mk(session, firm)
    text = "工程已完工。被告未经验收擅自使用，质量抗辩不成立【S1】。故应支付工程款。"
    anchors = cite.reanchor(session, "draft", row.id, "facts", text)

    assert len(anchors) == 1
    a = anchors[0]
    assert text[a.start:a.end] == "【S1】"
    assert "擅自使用" in a.sentence_text
    assert "工程已完工" not in a.sentence_text, "锚到了错误的句子"


def test_anchors_do_not_drift_after_editing(session, firm):
    """★★ 这条是本模块的立身之本：律师改了正文，锚点必须还是对的。

    手工维护偏移量的方案在第一次编辑之后就全错了。
    我们从正文里的【S数字】现推，所以永远不会漂。
    """
    row = _mk(session, firm)
    v1 = "被告擅自使用【S1】。"
    cite.reanchor(session, "draft", row.id, "facts", v1)

    # 律师在前面插了一大段
    v2 = ("2023年3月10日，双方签订《建设工程施工合同》，约定由原告承建案涉工程。"
          "工程于2025年3月20日完工。被告擅自使用【S1】。")
    anchors = cite.reanchor(session, "draft", row.id, "facts", v2)

    assert len(anchors) == 1
    a = anchors[0]
    assert v2[a.start:a.end] == "【S1】", "偏移量漂了 —— 指到了别的字符上"
    assert "被告擅自使用" in a.sentence_text


def test_reanchor_replaces_not_accumulates(session, firm):
    """重算是**替换**。累加的话，改几次正文锚点就翻倍了。"""
    row = _mk(session, firm)
    cite.reanchor(session, "draft", row.id, "facts", "甲【S1】。乙【S2】。")
    again = cite.reanchor(session, "draft", row.id, "facts", "只剩一个【S1】。")
    assert len(again) == 1


def test_bogus_ordinal_gets_no_anchor(session, firm):
    """引了不存在的编号，落不了锚点 —— 它本来就没有来源。

    （「这些是编的」由 draft 那边报警，这里不重复报。）
    """
    row = _mk(session, firm)
    anchors = cite.reanchor(session, "draft", row.id, "facts", "论断【S9】。")
    assert anchors == []


def test_same_citation_can_appear_twice(session, firm):
    row = _mk(session, firm)
    anchors = cite.reanchor(session, "draft", row.id, "facts",
                            "第一处【S1】。第二处也引它【S1】。")
    assert len(anchors) == 2
    assert {a.sentence_index for a in anchors} == {0, 1}


# ================================================================ 快照


def test_snapshot_keeps_everything_needed_to_reconstruct(session, firm):
    """★ 半年后检索库重建了，这份文书的引用还得还原得回来。"""
    row = _mk(session, firm)
    rows = cite.sources_of(session, "draft", row.id)
    c = rows[0]
    assert c.ordinal == 1
    assert c.source_id == "建工解释一_法释2020-25号#第十四条"
    assert "擅自使用" in c.snippet, "没存原文片段，库改版后就说不清当时依据的是什么"
    assert c.effective_status.startswith("现行有效")
    assert c.corpus_version == "1030d8027c-802b542b", "没存语料版本"
    assert c.trace_id == "abc123trace", "没存 trace_id，回不到当时那次检索"


def test_attach_replaces_the_whole_set(session, firm):
    """依据清单变了就整体替换 —— 半旧半新比全换更危险。"""
    row = _mk(session, firm)
    cite.reanchor(session, "draft", row.id, "facts", "论断【S1】。")
    cite.attach(session, firm["p"], "draft", row.id, SOURCES[:1])

    assert len(cite.sources_of(session, "draft", row.id)) == 1
    m = cite.sentence_map(session, "draft", row.id, [("facts", "事实与理由", "论断【S1】。")])
    assert m[0]["sentences"][0]["citations"] == [], "旧锚点没跟着清掉"


# ================================================================ ★ 点句子看出处


def test_sentence_map_marks_which_sentences_have_support(session, firm):
    """★★ 验收点：每句话有没有依据，一眼看得出。"""
    row = _mk(session, firm)
    text = "工程已完工。被告擅自使用，质量抗辩不成立【S1】。承包人享有优先受偿权【S2】。"
    cite.reanchor(session, "draft", row.id, "facts", text)

    part = cite.sentence_map(session, "draft", row.id,
                             [("facts", "事实与理由", text)])[0]
    sents = part["sentences"]
    assert len(sents) == 3
    assert sents[0]["citations"] == []           # 纯事实陈述
    assert sents[1]["citations"] == [1]
    assert sents[2]["citations"] == [2]
    # 展示用的正文里不该留着【S1】这种标记
    assert "【S" not in sents[1]["plain"]


def test_unsupported_flags_legal_assertions_without_citations(session, firm):
    """★ 「看着像法律论断但没标依据」要提示出来 —— 这是签字前要找的东西。"""
    row = _mk(session, firm)
    text = "工程于2025年3月20日完工。被告应当支付工程款并承担违约责任。"
    cite.reanchor(session, "draft", row.id, "facts", text)

    flagged = cite.unsupported(session, "draft", row.id,
                               [("facts", "事实与理由", text)])
    assert len(flagged) == 1
    assert "应当支付" in flagged[0]["text"]
    assert "2025年3月20日完工" not in flagged[0]["text"], "把纯事实陈述也标了"


def test_a_cited_assertion_is_not_flagged(session, firm):
    row = _mk(session, firm)
    text = "被告应当支付工程款【S1】。"
    cite.reanchor(session, "draft", row.id, "facts", text)
    assert cite.unsupported(session, "draft", row.id,
                            [("facts", "事实与理由", text)]) == []


# ================================================================ 溯源


def test_trace_answers_where_did_this_come_from(session, firm):
    """★ 律师问「你这条哪来的」，答案要是完整的一条链。"""
    row = _mk(session, firm)
    text = "被告擅自使用，质量抗辩不成立【S1】。"
    cite.reanchor(session, "draft", row.id, "facts", text)
    c = cite.sources_of(session, "draft", row.id)[0]

    t = cite.trace(session, firm["p"], c.id)
    assert t["source_id"] == "建工解释一_法释2020-25号#第十四条"
    assert "擅自使用" in t["snippet"]
    assert t["effective_status"].startswith("现行有效")
    assert t["corpus_version"] and t["trace_id"]
    assert t["issue"] == "未经验收擅自使用的后果"
    assert t["stale"] is False
    assert t["used_in"] and "擅自使用" in t["used_in"][0]["sentence"]


def test_trace_marks_stale_sources(session, firm):
    row = _mk(session, firm)
    stale = [c for c in cite.sources_of(session, "draft", row.id)
             if c.ordinal == 3][0]
    t = cite.trace(session, firm["p"], stale.id)
    assert t["stale"] is True, "已失效的依据没标出来 —— 引它是事故"


def test_trace_refuses_other_tenants(session, firm):
    row = _mk(session, firm)
    c = cite.sources_of(session, "draft", row.id)[0]
    other = type(firm["p"])(**{**firm["p"].__dict__, "tenant_id": firm["p"].tenant_id + 999}) \
        if hasattr(firm["p"], "__dict__") else None
    if other is None:
        pytest.skip("Principal 不是普通对象，跳过")
    with pytest.raises(NotFound):
        cite.trace(session, other, c.id)


# ================================================================ 覆盖率


def test_coverage_reports_the_shape_of_the_document(session, firm):
    row = _mk(session, firm)
    text = "工程已完工。被告擅自使用【S1】。被告应当支付工程款。"
    cite.reanchor(session, "draft", row.id, "facts", text)

    cov = cite.coverage(session, "draft", row.id, [("facts", "事实与理由", text)])
    assert cov["sentences"] == 3
    assert cov["cited"] == 1
    assert cov["flagged"] == 1                   # 「应当支付」没依据
    assert cov["sources"] == 3
    assert 2 in cov["unused"] and 3 in cov["unused"]
    assert cov["stale"] == [3]


# ================================================================ 与 draft 打通


def test_draft_wires_citations_automatically(session, firm, fakes):
    """★ 建文书就挂好依据，生成一节就落好锚点 —— 不用业务侧手动调。"""
    fakes.text = "被告擅自使用，质量抗辩不成立【S1】。"
    row = draft.create(session, firm["p"], firm["case"].id, "civil_complaint",
                       queries=["建设工程 未经竣工验收 擅自使用"])
    # 依据来自真检索会变，这里换成固定的再验锚点
    cite.attach(session, firm["p"], "draft", row.id, SOURCES)
    row.citations = json.dumps(SOURCES, ensure_ascii=False)
    session.flush()

    draft.generate_section(session, firm["p"], row.id, "facts")

    parts = draft.parts_of(session, row.id)
    m = cite.sentence_map(session, "draft", row.id, parts)
    facts = [p for p in m if p["key"] == "facts"][0]
    assert any(s["citations"] == [1] for s in facts["sentences"]), \
        "生成之后没有自动落锚点"


def test_editing_a_section_reanchors(session, firm, fakes):
    row = _mk(session, firm)
    draft.edit_section(session, firm["p"], row.id, "facts", "先写这样【S1】。")
    draft.edit_section(session, firm["p"], row.id, "facts",
                       "改成引另一条【S2】。")

    parts = draft.parts_of(session, row.id)
    m = cite.sentence_map(session, "draft", row.id, parts)
    facts = [p for p in m if p["key"] == "facts"][0]
    ords = [o for s in facts["sentences"] for o in s["citations"]]
    assert ords == [2], "改完没重算锚点：{0}".format(ords)


def test_sentence_is_rendered_in_place_not_stripped(session, firm):
    """★ 引用标记要**原位**渲染成上标，不能剥掉再补到句末。

    「根据【S1】，建设工程未经竣工验收…」剥掉标记会读成
    「根据，建设工程未经竣工验收…」—— 标记本来夹在「根据」和逗号之间。
    律师读的是这句话本身，不能为了标注方便把它读乱。
    """
    row = _mk(session, firm)
    text = "根据【S1】，发包人擅自使用的不予支持。"
    cite.reanchor(session, "draft", row.id, "facts", text)
    s = cite.sentence_map(session, "draft", row.id,
                          [("facts", "事实与理由", text)])[0]["sentences"][0]

    segs = s["segments"]
    assert segs[0]["t"] == "根据"
    assert segs[1]["cite"] == 1
    assert segs[2]["t"].startswith("，发包人")
    # 拼回去必须和原文一模一样（去掉标记本身）
    assert "".join(x.get("t", "") for x in segs) == "根据，发包人擅自使用的不予支持。"


# ================================================================ 引用体检


class _StubRetrieval:
    """假的检索服务。**测试要能造出"法条变了"这个场景** ——
    等真的法条修订再来验，那就永远验不了。"""

    def __init__(self, items, version="new-version"):
        self._items = items
        self._version = version

    def lookup(self, source_ids):
        return {"items": {k: v for k, v in self._items.items() if k in source_ids},
                "corpus_version": self._version}


def _with_retrieval(stub):
    """临时把检索服务换成桩，用完精确还原（只动这一个键）。"""
    from kernel.container import container

    had = "retrieval" in container._factories
    old = container._factories.get("retrieval")
    container.bind("retrieval", lambda: stub)

    class _Ctx:
        def __enter__(self):
            return stub

        def __exit__(self, *a):
            container._instances.pop("retrieval", None)
            if had:
                container._factories["retrieval"] = old
            else:
                container._factories.pop("retrieval", None)
    return _Ctx()


def test_recheck_says_ok_when_nothing_changed(session, firm):
    """没变的不打扰 —— 一片红的报告和一片绿的一样没人看。"""
    row = _mk(session, firm)
    same = {s["source_id"]: {"found": True, "title": s["title"],
                             "snippet": s["snippet"],
                             "effective_status": s["effective_status"]}
            for s in SOURCES}
    with _with_retrieval(_StubRetrieval(same)):
        out = cite.recheck(session, firm["p"], "draft", row.id)
    assert out["total"] == 3
    assert out["ok"] == 3
    assert out["changed"] == out["expired"] == out["gone"] == 0


def test_recheck_catches_amended_text(session, firm):
    """★ 同一文号、内容被修订 —— 这是最难发现的一种。

    source_id 没变，指过去还是这一条，但**原文已经不是当时那段了**。
    唯一能发现它的办法就是拿当时冻结的 snippet 去比。
    """
    row = _mk(session, firm)
    now = {s["source_id"]: {"found": True, "title": s["title"],
                            "snippet": s["snippet"],
                            "effective_status": s["effective_status"]}
           for s in SOURCES}
    now["建工解释一_法释2020-25号#第十四条"]["snippet"] = \
        "建设工程未经竣工验收，发包人擅自使用后，**又以质量不合格为由主张权利的，不予支持**……"

    with _with_retrieval(_StubRetrieval(now)):
        out = cite.recheck(session, firm["p"], "draft", row.id)
    assert out["changed"] == 1
    item = next(i for i in out["items"] if i["state"] == "changed")
    # ★ 两边原文都要给出来 —— 只说"变了"，律师没法判断变得要不要紧
    assert item["snippet_then"] and item["snippet_now"]
    assert item["snippet_then"] != item["snippet_now"]


def test_recheck_catches_newly_expired(session, firm):
    """★ 当时有效、现在失效 —— 引了废止条文是执业事故。"""
    row = _mk(session, firm)
    now = {s["source_id"]: {"found": True, "title": s["title"],
                            "snippet": s["snippet"],
                            "effective_status": s["effective_status"]}
           for s in SOURCES}
    now["民法典_合同编#第八百零七条"]["effective_status"] = "已失效（被XX法废止）"

    with _with_retrieval(_StubRetrieval(now)):
        out = cite.recheck(session, firm["p"], "draft", row.id)
    assert out["expired"] == 1
    item = next(i for i in out["items"] if i["state"] == "expired")
    assert "当时是" in item["note"] and "现在是" in item["note"]


def test_already_expired_when_cited_is_not_reported_again(session, firm):
    """★ 当时就标着「已失效」的，不再报一次。

    律师当时就知道它失效（可能是按行为时法故意引的）。
    再报一遍是噪音，而噪音会让人忽略真警报。
    """
    row = _mk(session, firm)
    now = {s["source_id"]: {"found": True, "title": s["title"],
                            "snippet": s["snippet"],
                            "effective_status": s["effective_status"]}
           for s in SOURCES}
    with _with_retrieval(_StubRetrieval(now)):
        out = cite.recheck(session, firm["p"], "draft", row.id)
    # 「合同法#第二百八十六条」当时就是已失效
    item = next(i for i in out["items"] if i["source_id"] == "合同法#第二百八十六条")
    assert item["state"] == "ok"


def test_recheck_reports_gone_and_keeps_the_frozen_copy(session, firm):
    """★ 库里找不到了，当时冻结的原文是唯一的退路。"""
    row = _mk(session, firm)
    now = {s["source_id"]: {"found": True, "title": s["title"],
                            "snippet": s["snippet"],
                            "effective_status": s["effective_status"]}
           for s in SOURCES}
    now["建工解释一_法释2020-25号#第十四条"] = {
        "found": False, "reason": "这部法规还在，但「第十四条」这一条找不到了"}

    with _with_retrieval(_StubRetrieval(now)):
        out = cite.recheck(session, firm["p"], "draft", row.id)
    assert out["gone"] == 1
    item = next(i for i in out["items"] if i["state"] == "gone")
    assert "第十四条" in item["note"]
    # ★ 快照还在 —— 这是当初冻结 snippet 的全部理由
    assert item["snippet_then"].startswith("建设工程未经竣工验收")


def test_recheck_says_unknown_when_service_is_down(session, firm):
    """★ 查不动就说查不动，不假装没问题。

    检索服务挂了返回一片绿色，比不体检更糟 —— 那是**假的安心**。
    """
    from kernel.container import container

    row = _mk(session, firm)
    had = "retrieval" in container._factories
    old = container._factories.get("retrieval")
    container._factories.pop("retrieval", None)
    container._instances.pop("retrieval", None)
    try:
        out = cite.recheck(session, firm["p"], "draft", row.id)
        assert out["unknown"] == 3
        assert out["ok"] == 0
        assert all("不可用" in i["note"] for i in out["items"])
    finally:
        if had:
            container._factories["retrieval"] = old


def test_recheck_on_a_draft_without_citations(session, firm):
    """没有引用的文书体检不该炸。"""
    row = draft.create(session, firm["p"], firm["case"].id, "civil_complaint")
    out = cite.recheck(session, firm["p"], "draft", row.id)
    assert out["total"] == 0 and out["items"] == []
