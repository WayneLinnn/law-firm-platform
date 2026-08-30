# -*- coding: utf-8 -*-
"""Step 12 · 文书生成。

两条主线：

  一、**能填的绝不让模型写。** 当事人名称、法院、证据清单都是案件数据，
      让模型"生成"它们等于让它照上下文猜一个名字 —— 猜错一个字，
      立案窗口就退回。

  二、**防幻觉靠校验不靠祈祷。** 提示词里写「只用给定依据」只是请求；
      真正管用的是生成之后逐个核对每个【S数字】。引了不存在的编号 =
      编的，定稿时硬拦。
"""
from __future__ import annotations

import json

import pytest

from kernel.container import container
from kernel.errors import DomainError, NotFound, PermissionDenied
from kernel.ports.model import ModelResult
from modules.case import service as case_svc
from modules.crm import service as crm
from modules.draft import service
from modules.draft import templates as tpl
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
    """可编程的假模型：想让它写什么就写什么，用来构造幻觉场景。"""

    def __init__(self):
        self.text = "被告未按约定支付工程款，应承担违约责任【S1】。"
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append((messages, kw))
        body = self.text(messages, kw) if callable(self.text) else self.text
        return ModelResult(text=body, usage={"cost_usd": 0.004})


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
    t = identity.create_tenant(session, "dr-" + sfx, "康桥")
    identity.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in case_svc.ensure_default_causes(session, t.id)}

    root = identity.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    t1 = identity.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                  kind="team", parent_id=root.id)
    t2 = identity.create_org_unit(session, t.id, "李团队", "t2-" + sfx,
                                  kind="team", parent_id=root.id)

    def person(name, org, role):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, "senior_partner")
        identity.assign_membership(session, u.id, org.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    wang = person("wang", t1, "committee")
    zhang = person("zhang", t2, "lawyer")

    ours = crm.create_party(session, t.id, "宏远建设集团有限公司", allow_similar=True)
    theirs = crm.create_party(session, t.id, "凯达置业有限公司", allow_similar=True)
    case = case_svc.create_case(
        session, t.id, "AJ" + sfx[:6], "宏远诉凯达", lead_id=wang.id,
        cause_id=causes["construction"].id, org_unit_id=t1.id,
        court="青岛市中级人民法院", summary="工程完工未验收即使用，欠付工程款1240万元。")
    case_svc.add_party(session, t.id, case.id, ours.id, role="plaintiff", is_ours=True)
    case_svc.add_party(session, t.id, case.id, theirs.id, role="defendant", is_ours=False)

    return {"tenant": t, "case": case, "wang": wang,
            "p_wang": access.build_principal(session, wang.id),
            "p_zhang": access.build_principal(session, zhang.id)}


CITES = [
    {"source_id": "建工解释一_法释2020-25号#第十四条", "title": "建工解释一",
     "snippet": "未经竣工验收擅自使用的……", "effective_status": "现行有效"},
    {"source_id": "民法典_合同编#第八百零七条", "title": "民法典",
     "snippet": "建设工程价款优先受偿……", "effective_status": "现行有效"},
]


def _mk(session, firm, key="civil_complaint"):
    row = service.create(session, firm["p_wang"], firm["case"].id, key)
    row.citations = json.dumps(CITES, ensure_ascii=False)
    session.flush()
    return row


# ================================================================ ★ 引用校验


def test_fabricated_citation_is_caught():
    """★★ 本模块的立身之本：引了不存在的编号 = 编的，必须抓出来。"""
    used, warns = service.verify_citations(
        "承包人享有优先受偿权【S2】，且质量抗辩不成立【S7】。", CITES)
    assert used == [2]
    assert any("这些是编的" in w and "S7" in w for w in warns)


def test_no_citation_at_all_is_flagged():
    used, warns = service.verify_citations("被告应当支付工程款。", CITES)
    assert used == []
    assert any("没有引用任何依据" in w for w in warns)


def test_repealed_citation_is_flagged():
    """引到已废止的条文 —— 内容对得上，但不能用了。"""
    dead = [{"source_id": "合同法#第二百八十六条", "effective_status": "已失效"}]
    used, warns = service.verify_citations("依据【S1】主张优先受偿。", dead)
    assert used == [1]
    assert any("已失效" in w and "不能再作为依据" in w for w in warns)


def test_clean_citation_has_no_warnings():
    used, warns = service.verify_citations("依据【S1】与【S2】，请求成立。", CITES)
    assert used == [1, 2] and warns == []


# ================================================================ 能填的直接填


def test_parties_and_court_are_filled_not_generated(session, firm, fakes):
    """★ 当事人和法院直接取案件数据 —— **一次模型都不该调**。"""
    row = _mk(session, firm)
    secs = {s.key: s for s in service.sections_of(session, row.id)}

    assert "宏远建设集团有限公司" in secs["plaintiff"].content
    assert "凯达置业有限公司" in secs["defendant"].content
    assert secs["court"].content == "青岛市中级人民法院"
    for k in ("plaintiff", "defendant", "court"):
        assert secs[k].source == "fill"
    assert fakes.calls == [], "建文书时不该调模型 —— 这些都是填出来的"


def test_missing_case_data_says_what_is_missing(session, firm):
    """缺数据要**明说缺什么**，不能悄悄留白让律师自己发现。"""
    case = case_svc.create_case(
        session, firm["tenant"].id, "AJX", "无当事人的案子",
        lead_id=firm["wang"].id)
    row = service.create(session, firm["p_wang"], case.id, "civil_complaint")
    secs = {s.key: s for s in service.sections_of(session, row.id)}
    assert secs["plaintiff"].content == ""
    assert "没有登记" in secs["plaintiff"].note
    assert "没有填法院" in secs["court"].note


def test_evidence_list_comes_from_the_case_file(session, firm):
    """证据清单取自案卷「证据材料」—— 用上 Step 11，不让人再抄一遍。"""
    from modules.doc import service as doc

    doc.upload(session, firm["p_wang"], firm["case"].id,
               filename="合同.txt", data="施工合同".encode("utf-8"),
               content_type="text/plain", title="施工合同原件", folder="证据材料")
    doc.upload(session, firm["p_wang"], firm["case"].id,
               filename="结算.txt", data="结算单".encode("utf-8"),
               content_type="text/plain", title="结算确认单", folder="证据材料")

    row = _mk(session, firm)
    ev = {s.key: s for s in service.sections_of(session, row.id)}["evidence"]
    assert "施工合同原件" in ev.content and "结算确认单" in ev.content


# ================================================================ 生成


def test_generate_records_used_citations(session, firm, fakes):
    fakes.text = "工程未经验收擅自使用，质量抗辩不成立【S1】；享有优先受偿权【S2】。"
    row = _mk(session, firm)
    sec = service.generate_section(session, firm["p_wang"], row.id, "facts")
    assert sec.source == "model"
    assert json.loads(sec.used) == [1, 2]
    assert json.loads(sec.warnings) == []


def test_generate_flags_fabrication_immediately(session, firm, fakes):
    """★ 模型编了个 S9，生成当场就标出来，不等到定稿。"""
    fakes.text = "被告应支付工程款【S9】。"
    row = _mk(session, firm)
    sec = service.generate_section(session, firm["p_wang"], row.id, "facts")
    warns = json.loads(sec.warnings)
    assert any("这些是编的" in w for w in warns)


def test_generation_never_turns_on_thinking(session, firm, fakes):
    """起草不开思考 —— Step 10 两次栽在「推理吃光配额、正文返回空串」上。"""
    row = _mk(session, firm)
    service.generate_section(session, firm["p_wang"], row.id, "facts")
    assert fakes.calls[-1][1].get("thinking") is False


def test_prompt_only_offers_the_given_citations(session, firm, fakes):
    """提示词里只能出现给定依据，并明确禁止引用别的。"""
    row = _mk(session, firm)
    service.generate_section(session, firm["p_wang"], row.id, "facts")
    prompt = fakes.calls[-1][0][-1].content
    assert "建工解释一_法释2020-25号#第十四条" in prompt
    assert "只能引这些" in prompt
    system = fakes.calls[-1][0][0].content
    assert "绝不编造" in system


def test_empty_model_output_is_an_error_not_an_empty_section(session, firm, fakes):
    """空正文要报错。静默留白 = 律师以为模型认为无话可说。"""
    fakes.text = "   "
    row = _mk(session, firm)
    with pytest.raises(DomainError) as e:
        service.generate_section(session, firm["p_wang"], row.id, "facts")
    assert e.value.code == "draft.empty_output"


def test_manual_sections_are_not_generated(session, firm):
    row = _mk(session, firm)
    with pytest.raises(DomainError) as e:
        service.generate_section(session, firm["p_wang"], row.id, "signature")
    assert e.value.code == "draft.manual_only"


# ================================================================ 不覆盖律师的修改


def test_regenerate_will_not_silently_overwrite_edits(session, firm, fakes):
    """★ 律师辛苦改完被一键重生成冲掉，是最让人火大的那种 bug。"""
    row = _mk(session, firm)
    service.generate_section(session, firm["p_wang"], row.id, "facts")
    service.edit_section(session, firm["p_wang"], row.id, "facts",
                         "我自己改过的版本【S1】。")

    with pytest.raises(DomainError) as e:
        service.generate_section(session, firm["p_wang"], row.id, "facts")
    assert e.value.code == "draft.would_overwrite"

    sec = service.generate_section(session, firm["p_wang"], row.id, "facts", force=True)
    assert sec.source == "model", "显式 force 之后应当覆盖"


def test_editing_revalidates_citations(session, firm):
    """律师手改也会写错编号 —— 改完照样校验。"""
    row = _mk(session, firm)
    sec = service.edit_section(session, firm["p_wang"], row.id, "facts",
                               "依据【S5】主张。")
    assert any("这些是编的" in w for w in json.loads(sec.warnings))


# ================================================================ 定稿


def test_cannot_finalize_with_fabricated_citations(session, firm, fakes):
    """★★ 这是「敢签字」的底线：带编造条号的文书一律不放行。"""
    fakes.text = "主张成立【S9】。"
    row = _mk(session, firm)
    for k in ("claims", "facts"):
        service.generate_section(session, firm["p_wang"], row.id, k)

    with pytest.raises(DomainError) as e:
        service.finalize(session, firm["p_wang"], row.id)
    assert e.value.code == "draft.fabricated_citation"


def test_cannot_finalize_with_empty_required_sections(session, firm):
    row = _mk(session, firm)
    with pytest.raises(DomainError) as e:
        service.finalize(session, firm["p_wang"], row.id)
    assert e.value.code == "draft.not_ready"
    assert "诉讼请求" in str(e.value) or "事实与理由" in str(e.value)


def test_readiness_lists_everything_blocking_at_once(session, firm):
    """★ 一次把拦路的都说全，别让人点一次报一个。"""
    row = _mk(session, firm)
    state = service.readiness(session, firm["p_wang"], row.id)
    assert not state["ready"]
    assert len(state["blocking"]) >= 2


def test_finalize_archives_into_the_case_file(session, firm, fakes):
    fakes.text = "依据【S1】，请求成立。"
    row = _mk(session, firm)
    for k in ("claims", "facts"):
        service.generate_section(session, firm["p_wang"], row.id, k)

    row = service.finalize(session, firm["p_wang"], row.id, note="已核对")
    assert row.status == "final" and row.document_id

    from modules.doc import service as doc

    d = doc.get_document(session, firm["p_wang"], row.document_id)
    assert d.folder == "诉讼文书"
    assert doc.search(session, firm["p_wang"], "请求成立")


def test_final_draft_cannot_be_voided(session, firm, fakes):
    fakes.text = "依据【S1】，请求成立。"
    row = _mk(session, firm)
    for k in ("claims", "facts"):
        service.generate_section(session, firm["p_wang"], row.id, k)
    service.finalize(session, firm["p_wang"], row.id)
    with pytest.raises(DomainError):
        service.void(session, firm["p_wang"], row.id)


# ================================================================ 组装与权限


def test_assemble_includes_the_citation_list(session, firm, fakes):
    """拼出来的文书末尾要带依据清单 —— 否则【S1】是什么没人知道。"""
    fakes.text = "依据【S1】，请求成立。"
    row = _mk(session, firm)
    service.generate_section(session, firm["p_wang"], row.id, "facts")
    text = service.assemble(session, firm["p_wang"], row.id)
    assert "## 依据" in text
    assert "[S1] 建工解释一_法释2020-25号#第十四条" in text
    assert "青岛市中级人民法院" in text


def test_draft_inherits_case_visibility(session, firm):
    row = _mk(session, firm)
    with pytest.raises((PermissionDenied, NotFound)):
        service.get_draft(session, firm["p_zhang"], row.id)
    with pytest.raises((PermissionDenied, NotFound)):
        service.drafts_of_case(session, firm["p_zhang"], firm["case"].id)


def test_unknown_template_is_refused(session, firm):
    with pytest.raises(NotFound):
        service.create(session, firm["p_wang"], firm["case"].id, "不存在的模板")


def test_templates_cover_the_common_documents():
    keys = {t["key"] for t in tpl.describe()}
    assert {"civil_complaint", "civil_defense", "lawyer_letter", "legal_opinion"} <= keys
    complaint = tpl.get("civil_complaint")
    kinds = {s.key: s.kind for s in complaint.sections}
    # ★ 当事人、法院必须是填的，不能是生成的
    assert kinds["plaintiff"] == tpl.FILL
    assert kinds["defendant"] == tpl.FILL
    assert kinds["court"] == tpl.FILL
    assert kinds["facts"] == tpl.GENERATE


def test_party_roles_are_written_in_chinese(session, firm):
    """★ 文书里印 "plaintiff" 是递不出去的。

    库里存英文枚举是对的（稳定、好比对），但落到纸上必须是中文。
    """
    row = _mk(session, firm)
    secs = {s.key: s for s in service.sections_of(session, row.id)}
    assert "（原告）" in secs["plaintiff"].content
    assert "（被告）" in secs["defendant"].content
    assert "plaintiff" not in secs["plaintiff"].content


def test_claim_list_is_not_nagged_about_citations(session, firm, fakes):
    """★ 「诉讼请求」是请求清单，本来就不引法条 —— 别报「没有依据」。

    噪音会让人开始忽略告警，等真的编造出现时也一起忽略掉。
    """
    fakes.text = "一、判令被告支付工程款1240万元；\n二、诉讼费由被告承担。"
    row = _mk(session, firm)
    sec = service.generate_section(session, firm["p_wang"], row.id, "claims")
    assert json.loads(sec.warnings) == [], "对请求清单报了引用告警"

    # 但「事实与理由」这种要讲道理的，照报不误
    sec2 = service.generate_section(session, firm["p_wang"], row.id, "facts")
    assert any("没有引用任何依据" in w for w in json.loads(sec2.warnings))


def test_fabrication_is_caught_even_where_citations_are_optional(session, firm, fakes):
    """不要求引用 ≠ 允许编造。"""
    fakes.text = "一、判令被告支付工程款【S9】。"
    row = _mk(session, firm)
    sec = service.generate_section(session, firm["p_wang"], row.id, "claims")
    assert any("这些是编的" in w for w in json.loads(sec.warnings))
