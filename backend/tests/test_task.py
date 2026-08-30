# -*- coding: utf-8 -*-
"""Step 14 + 15 · 待办与时限。

时限这块的头号原则：**算不出来要明说，绝不猜。**
算错的时限律师会信，算不出的他会自己去查。

所以重点测四件事：

  1. 算得对（月末退位、除斥期间、预警阈值）
  2. **算不出来时状态是 unknown 并说清缺哪个日期**
  3. **除斥期间不许中断** —— 那是法律性质，不是配置项
  4. 规则是数据：律所能加、能停用，加的时候必须填依据
"""
from __future__ import annotations

from datetime import date

import pytest

from kernel.errors import DomainError, NotFound, PermissionDenied
from modules.case import service as case_svc
from modules.identity import access
from modules.identity import service as identity
from modules.task import service


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


@pytest.fixture
def firm(session):
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = identity.create_tenant(session, "tk-" + sfx, "康桥")
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

    case = case_svc.create_case(
        session, t.id, "AJ" + sfx[:6], "宏远诉凯达", lead_id=wang.id,
        cause_id=causes["construction"].id, org_unit_id=t1.id)
    service.ensure_default_rules(session, t.id)

    return {"tenant": t, "case": case, "wang": wang, "zhang": zhang,
            "p": access.build_principal(session, wang.id),
            "pz": access.build_principal(session, zhang.id)}


# ================================================================ 算得对


@pytest.mark.parametrize("anchor,months,days,want", [
    ("2026-01-20", 6, 0, date(2026, 7, 20)),
    ("2025-08-31", 6, 0, date(2026, 2, 28)),      # 月末退位
    ("2024-08-31", 6, 0, date(2025, 2, 28)),
    ("2026-03-01", 0, 15, date(2026, 3, 16)),
    ("2026-01-20", 36, 0, date(2029, 1, 20)),
])
def test_date_math(anchor, months, days, want):
    assert service.plus(date.fromisoformat(anchor), months, days) == want


def test_parse_date_never_guesses():
    assert service.parse_date("2025年3月20日") == date(2025, 3, 20)
    assert service.parse_date("2025-03-20") == date(2025, 3, 20)
    assert service.parse_date("说不清") is None
    assert service.parse_date("") is None
    assert service.parse_date("2025-13-45") is None


# ================================================================ 生成


def test_generate_picks_rules_by_cause_and_role(session, firm):
    """建工案件只出建工 + 通用规则，不出劳动争议那几条。"""
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"payable_on": "2026-01-20"},
                            role="contractor", today=date(2026, 3, 1))
    codes = {r.rule_code for r in rows}
    assert "construction.priority" in codes
    assert "civil.limitation" in codes            # 通用规则（cause_code 为空）
    assert not any(c.startswith("labor.") for c in codes), "劳动争议规则不该出现"


def test_priority_deadline_is_six_months(session, firm):
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"payable_on": "2026-01-20"},
                            role="contractor", today=date(2026, 3, 1))
    p = [r for r in rows if r.rule_code == "construction.priority"][0]
    assert p.due_on == date(2026, 7, 20)
    assert p.fatal is True
    assert p.status == "ok"


def test_expired_fatal_says_the_right_is_gone(session, firm):
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"payable_on": "2025-04-20"},
                            role="contractor", today=date(2026, 8, 27))
    p = [r for r in rows if r.rule_code == "construction.priority"][0]
    assert p.status == "expired"
    d = service.describe(p, today=date(2026, 8, 27))
    assert "权利已丧失" in d["message"]


def test_warn_window_comes_from_the_rule(session, firm):
    """预警提前量是**规则里配的**，不同期限不一样（优先受偿权 60 天）。"""
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"payable_on": "2026-01-20"},
                            role="contractor", today=date(2026, 6, 1))
    p = [r for r in rows if r.rule_code == "construction.priority"][0]
    assert p.warn_days == 60
    assert p.status == "urgent"                   # 7/20 - 6/1 = 49 天 < 60


def test_missing_anchor_is_unknown_and_names_the_field(session, firm):
    """★★ 算不出来必须明说缺哪个日期，不能静默跳过。

    静默跳过 = 律师以为「系统没报警所以没问题」—— 最坏的一种错。
    """
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={}, role="contractor", today=date(2026, 8, 27))
    p = [r for r in rows if r.rule_code == "construction.priority"][0]
    assert p.status == "unknown" and p.due_on is None

    d = service.describe(p, today=date(2026, 8, 27))
    assert "应付款日期" in d["message"], "只说算不出来、不说缺什么，等于没说"
    assert "必须补齐" in d["message"]


def test_every_deadline_carries_a_caveat(session, firm):
    """★ 每条时限都要说清它盖不住什么 —— 产品上是提示，不是承诺。"""
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"payable_on": "2026-01-20"}, role="contractor")
    p = [r for r in rows if r.rule_code == "construction.priority"][0]
    assert p.caveat and "律师确认" in p.caveat
    assert p.basis, "没有法律依据的时限，律师不敢信也不该信"


def test_fatal_and_expired_sort_first(session, firm):
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"payable_on": "2020-01-01"},
                            role="contractor", today=date(2026, 8, 27))
    assert rows[0].status == "expired" and rows[0].fatal


def test_regenerate_does_not_clobber_confirmed(session, firm):
    """★ 律师确认过的起算日，重新生成时不许覆盖 —— 人的判断优先。"""
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2026-01-20"}, role="contractor")
    rows = service.deadlines_of(session, firm["p"], firm["case"].id)
    p = [r for r in rows if r.rule_code == "construction.priority"][0]
    service.set_anchor(session, firm["p"], p.id, date(2026, 5, 20), "以结算单为准")

    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2026-01-20"}, role="contractor")
    again = [r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
             if r.rule_code == "construction.priority"][0]
    assert again.anchor_on == date(2026, 5, 20), "把律师确认过的日期覆盖掉了"


# ================================================================ ★ 中断


def test_fatal_deadline_cannot_be_interrupted(session, firm):
    """★★ 除斥期间**不发生中断/中止** —— 这是法律性质，不是配置项。

    如果允许「登记中断」把优先受偿权往后延，等于给律师一个错误的安全感。
    """
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2026-01-20"}, role="contractor")
    p = [r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
         if r.rule_code == "construction.priority"][0]

    with pytest.raises(DomainError) as e:
        service.interrupt(session, firm["p"], p.id, date(2026, 5, 1), "发了催款函")
    assert e.value.code == "task.not_suspendable"
    assert "除斥期间" in str(e.value)


def test_limitation_can_be_interrupted_and_restarts(session, firm):
    """诉讼时效可中断 —— 从中断日重新起算三年。"""
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2024-01-20"}, role="contractor")
    lim = [r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
           if r.rule_code == "construction.limitation"][0]
    assert lim.due_on == date(2027, 1, 20)

    service.interrupt(session, firm["p"], lim.id, date(2026, 5, 1),
                      "已发催款函并由对方签收，EMS 单号 xxx")
    assert lim.due_on == date(2029, 5, 1), "中断后没有重新起算"
    assert lim.interrupt_note.startswith("已发催款函")


def test_interrupt_requires_evidence_note(session, firm):
    """中断必须写事由和证据 —— 事后要拿得出来。"""
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2024-01-20"}, role="contractor")
    lim = [r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
           if r.rule_code == "construction.limitation"][0]
    with pytest.raises(DomainError):
        service.interrupt(session, firm["p"], lim.id, date(2026, 5, 1), "   ")


# ================================================================ 规则是数据


def test_firm_can_add_its_own_rule(session, firm):
    r = service.create_rule(
        session, firm["p"], code="firm.internal_review",
        name="内部复核提前量", basis="本所执业规范第 12 条",
        anchor_field="served_on", days=7, warn_days=2,
        caveat="只是本所内部提前量，不是法定期限")
    assert r.builtin is False and r.active is True


def test_new_rule_must_have_a_basis(session, firm):
    """★ 没有依据的时限，律师不敢信也不该信。"""
    with pytest.raises(DomainError) as e:
        service.create_rule(session, firm["p"], code="x", name="随便一个",
                            basis="  ", anchor_field="served_on", days=5)
    assert e.value.code == "task.rule_basis_required"


def test_disabled_rule_stops_generating(session, firm):
    service.set_rule_active(session, firm["p"], "construction.priority", False)
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"payable_on": "2026-01-20"}, role="contractor")
    assert "construction.priority" not in {r.rule_code for r in rows}


def test_seeding_twice_does_not_overwrite_edits(session, firm):
    """内置规则灌第二次不覆盖 —— 律所可能改过。"""
    service.set_rule_active(session, firm["p"], "construction.priority", False)
    service.ensure_default_rules(session, firm["tenant"].id)
    rules = {r.code: r for r in service.list_rules(session, firm["p"],
                                                   include_inactive=True)}
    assert rules["construction.priority"].active is False


# ================================================================ 任务


def test_task_lifecycle(session, firm):
    t = service.create_task(session, firm["p"], "起草起诉状",
                            case_id=firm["case"].id, due_on=date(2026, 9, 10),
                            priority="high")
    assert t.status == "todo" and t.assignee_id == firm["wang"].id

    service.update_task(session, firm["p"], t.id, status="done")
    assert t.status == "done" and t.done_by == firm["wang"].id and t.done_at


def test_task_needs_a_title(session, firm):
    with pytest.raises(DomainError):
        service.create_task(session, firm["p"], "   ")


# ================================================================ 权限


def test_deadlines_inherit_case_visibility(session, firm):
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2026-01-20"}, role="contractor")
    with pytest.raises((PermissionDenied, NotFound)):
        service.deadlines_of(session, firm["pz"], firm["case"].id)


def test_board_only_shows_visible_cases(session, firm):
    """★ 待办看板最容易漏权限 —— 它是跨案件聚合的。"""
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2026-01-20"}, role="contractor")

    mine = service.board(session, firm["p"])
    assert mine["deadlines"], "自己的案子应当看得到"

    theirs = service.board(session, firm["pz"])
    assert theirs["deadlines"] == [], "张三看到了他没权限的案件时限"


def test_board_counts_what_needs_attention(session, firm):
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2020-01-01"},
                     role="contractor", today=date(2026, 8, 27))
    b = service.board(session, firm["p"], today=date(2026, 8, 27))
    assert b["expired"] >= 1
    assert b["deadlines"][0].status == "expired", "过期的没排最前面"


def test_mark_done_drops_off_the_board(session, firm):
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2020-01-01"},
                     role="contractor", today=date(2026, 8, 27))
    rows = service.deadlines_of(session, firm["p"], firm["case"].id,
                                today=date(2026, 8, 27))
    for r in rows:
        service.mark(session, firm["p"], r.id, "waived", "本案不适用")
    b = service.board(session, firm["p"], today=date(2026, 8, 27))
    assert b["deadlines"] == []


def test_manual_deadline_for_what_rules_cannot_cover(session, firm):
    """规则盖不住的手工加 —— 比如"设计文件规定的合理使用年限"。"""
    d = service.add_deadline(
        session, firm["p"], firm["case"].id,
        name="主体结构合理使用年限届满", basis="设计文件载明 50 年",
        due_on=date(2075, 3, 20), note="查设计说明书第 3 页")
    assert d.source == "manual" and d.confirmed_at


def test_seed_is_idempotent_and_returns_the_whole_set(session, firm):
    """★ 幂等接口的返回值要描述**结果**，不是增量。

    第一版这里返回「这次新建的」，第二次调用返回空列表 —— 界面据此
    渲染，会显示成「本所一条规则都没有」。
    """
    tid = firm["tenant"].id
    first = service.ensure_default_rules(session, tid)
    second = service.ensure_default_rules(session, tid)
    assert len(second) == len(first) > 0
    assert {r.code for r in second} == {r.code for r in first}
    # 而且确实没在库里翻倍
    rows = service.list_rules(session, firm["p"])
    assert len({r.code for r in rows}) == len(rows)


def test_custom_rule_must_use_a_known_anchor_field(session, firm):
    """★ 起算字段是封闭词表。

    随便写一个字段名的规则，generate() 永远取不到值，会变成一条
    **永远停在「算不出」**的僵尸规则，一直占着看板的红色计数；
    而且界面上会显示成「缺『signed_on』」，等于没提示。
    """
    with pytest.raises(DomainError) as e:
        service.create_rule(session, firm["p"], code="x.custom", name="自定义",
                            basis="合同第 8 条", anchor_field="signed_on", days=30)
    assert e.value.code == "task.rule_anchor_unknown"
    # 报错要把能用的字段列出来，否则律所只能靠猜
    assert "竣工日期" in str(e.value)

    # 词表内的字段照常能建
    row = service.create_rule(session, firm["p"], code="x.custom", name="自定义",
                              basis="合同第 8 条", anchor_field="settled_on", days=30)
    assert row.anchor_field == "settled_on"


def test_describe_labels_every_known_anchor_field(session, firm):
    """界面要显示「缺『竣工日期』」而不是「缺『completed_on』」。"""
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={}, role="contractor")
    for r in rows:
        d = service.describe(r)
        assert d["anchor_label"] != d["anchor_field"],             "{0} 的起算字段 {1} 没有中文名".format(r.name, r.anchor_field)


def test_manual_deadline_has_no_anchor_label(session, firm):
    """★ 手工加的时限没有起算字段 —— 界面不能渲染成「缺『』」。

    空括号看着像数据坏了，比什么都不显示更让人不安。
    """
    d = service.add_deadline(session, firm["p"], firm["case"].id,
                             "举证期限", "受理通知书载明", date(2026, 9, 1))
    out = service.describe(d)
    assert out["anchor_label"] == ""
    assert out["source"] == "manual"


def test_rule_without_a_period_gets_its_own_status(session, firm):
    """★ 「没有固定期间」不是「算不出」。

    质量责任是「设计使用年限内」，没有可加的期间。第一版把它和缺起算日
    一样标成 unknown，于是律师把竣工日期填进去 —— 它还是红的。
    一条永远变不成绿色的红字，两周内就会被无视，然后旁边真正致命的
    「算不出」跟着一起被无视。**假警报会吃掉真警报。**
    """
    rows = service.generate(session, firm["p"], firm["case"].id,
                            anchors={"completed_on": "2026-01-10"},
                            role="contractor", today=date(2026, 3, 1))
    quality = next(r for r in rows if r.rule_code == "construction.quality")
    assert quality.status == "no_fixed"
    assert service.describe(quality)["message"].startswith("没有固定期间")

    # 填了起算日也不会变成 unknown —— 它不该催人补任何东西
    again = service.generate(session, firm["p"], firm["case"].id,
                             anchors={"completed_on": "2026-01-10"},
                             role="contractor", today=date(2026, 3, 1))
    assert next(r for r in again
                if r.rule_code == "construction.quality").status == "no_fixed"


def test_no_fixed_does_not_inflate_the_urgent_counts(session, firm):
    """看板上的红色计数只数「补齐就能算」的，不数「本来就没期间」的。"""
    service.generate(session, firm["p"], firm["case"].id, anchors={},
                     role="contractor", today=date(2026, 3, 1))
    b = service.board(session, firm["p"], today=date(2026, 3, 1))
    no_fixed = [d for d in b["deadlines"] if d.status == "no_fixed"]
    assert no_fixed, "本案应当有没有固定期间的时限"
    assert b["unknown"] == len([d for d in b["deadlines"] if d.status == "unknown"])
    assert all(d.status != "no_fixed"
               for d in b["deadlines"] if d.status == "unknown")


def test_refusal_reasons_are_not_conflated(session, firm):
    """★ 拒绝中断有三种理由，不能共用一句「是除斥期间」。

    律师会照着这句话理解一条期限的**法律性质**。把一条只是没标可中断的
    普通规则说成除斥期间，是在界面上讲错法律。
    """
    rows = service.generate(session, firm["p"], firm["case"].id, anchors={},
                            role="contractor", today=date(2026, 3, 1))
    by = {r.rule_code: r for r in rows}

    # ① 真·除斥期间
    with pytest.raises(DomainError) as e1:
        service.interrupt(session, firm["p"], by["construction.priority"].id,
                          date(2026, 3, 1), "发了函")
    assert e1.value.code == "task.not_suspendable"
    assert "除斥期间" in str(e1.value)

    # ② 没有固定期间 —— 不是除斥期间，别这么说
    with pytest.raises(DomainError) as e2:
        service.interrupt(session, firm["p"], by["construction.quality"].id,
                          date(2026, 3, 1), "发了函")
    assert e2.value.code == "task.no_fixed_period"
    assert "除斥期间" not in str(e2.value)


def test_describe_says_whether_it_can_be_interrupted(session, firm):
    """界面不该按 fatal 自己猜能不能中断。"""
    rows = service.generate(session, firm["p"], firm["case"].id, anchors={},
                            role="contractor", today=date(2026, 3, 1))
    by = {r.rule_code: service.describe(r) for r in rows}
    assert by["construction.priority"]["can_interrupt"] is False   # 除斥
    assert by["construction.quality"]["can_interrupt"] is False    # 无固定期间
    assert by["construction.limitation"]["can_interrupt"] is True  # 普通时效


# ---------------------------------------------------------------- 中止 / 最长保护期


def test_suspension_is_six_months_from_when_the_obstacle_ends(session, firm):
    """★ 中止（194 条）：从事由**消除**之日起满六个月届满。

    不是「暂停多久就往后推多久」，也不是从事由**发生**之日算。
    """
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2023-01-10"},
                     role="contractor", today=date(2026, 1, 1))
    row = next(r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
               if r.rule_code == "construction.limitation")
    # 正常届满日：2023-01-10 + 3 年 = 2026-01-10
    assert row.due_on == date(2026, 1, 10)

    out = service.suspend(session, firm["p"], row.id, date(2025, 12, 20),
                          "疫情封控，无法主张权利，见社区证明")
    # 事由消除 2025-12-20 起满六个月 = 2026-06-20，比原届满日晚，取它
    assert out.due_on == date(2026, 6, 20)
    assert out.suspend_end_on == date(2025, 12, 20)


def test_suspension_never_shortens_the_period(session, firm):
    """中止只保证「至少还有六个月」，不能把本来更晚的届满日提前。"""
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2025-01-10"},
                     role="contractor", today=date(2025, 6, 1))
    row = next(r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
               if r.rule_code == "construction.limitation")
    assert row.due_on == date(2028, 1, 10)
    out = service.suspend(session, firm["p"], row.id, date(2025, 6, 1), "障碍已消除")
    # 2025-06-01 + 6 个月 = 2025-12-01，比 2028-01-10 早 —— 不能采用
    assert out.due_on == date(2028, 1, 10)


def test_interruption_does_not_move_the_original_anchor(session, firm):
    """★ 中断不改变「权利受损害之日」这个事实。

    第一版把 anchor_on 改成了中断日，后果是最长保护期跟着一起往后跑 ——
    20 年上限被自己顶穿，等于没有上限。
    """
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2010-01-10"},
                     role="contractor", today=date(2012, 1, 1))
    row = next(r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
               if r.rule_code == "construction.limitation")
    out = service.interrupt(session, firm["p"], row.id, date(2012, 5, 1),
                            "发出催款函，EMS 底单在卷", today=date(2012, 6, 1))
    assert out.anchor_on == date(2010, 1, 10), "起算日被中断日覆盖了"
    assert out.interrupted_on == date(2012, 5, 1)
    assert out.due_on == date(2015, 5, 1)          # 从中断日重新起算 3 年
    assert out.outer_due_on == date(2030, 1, 10)   # 从**原始**起算日 + 20 年


def test_repeated_interruptions_cannot_punch_through_twenty_years(session, firm):
    """★ 这条测试是整个时限引擎存在的理由。

    一个每年发一次催款函的案子，每次中断都把届满日推后三年。不设上限的话，
    系统会一路显示「正常」到第 21 年 —— 一个笃定的绿色，底下什么都没有。
    《民法典》188 条 2 款的 20 年**不适用中断中止**。
    """
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2005-03-01"},
                     role="contractor", today=date(2006, 1, 1))
    row = next(r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
               if r.rule_code == "construction.limitation")

    # 连着中断到 2024 年，每次都「重新起算三年」
    for year in range(2006, 2025):
        row = service.interrupt(session, firm["p"], row.id, date(year, 3, 1),
                                "第 %d 次催款" % year, today=date(year, 4, 1))

    assert row.outer_due_on == date(2025, 3, 1), "最长保护期应当从 2005-03-01 起算"
    assert row.due_on == date(2025, 3, 1), "届满日被中断顶穿了 20 年上限"
    assert service.describe(row)["capped"] is True
    # 到了 2026 年，它必须是「已过期」，不能还显示正常
    assert service._status_of(row, date(2026, 1, 1)) == "expired"


def test_capped_deadline_says_further_interruption_is_pointless(session, firm):
    """顶到上限时要明说「再中断也没用」—— 否则界面上它只是个普通日期。"""
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2005-03-01"},
                     role="contractor", today=date(2006, 1, 1))
    row = next(r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
               if r.rule_code == "construction.limitation")
    row = service.interrupt(session, firm["p"], row.id, date(2024, 3, 1),
                            "催款", today=date(2024, 4, 1))
    d = service.describe(row, today=date(2024, 4, 1))
    assert d["capped"] is True
    assert "再发生中断也不会往后延" in d["message"]


def test_only_limitation_rules_have_an_outer_cap(session, firm):
    """仲裁时效、执行期限、除斥期间都没有 20 年上限 —— 别乱套。"""
    service.ensure_default_rules(session, firm["tenant"].id)
    by = {r.code: r for r in service.list_rules(session, firm["p"])}
    assert by["construction.limitation"].outer_years == 20
    assert by["civil.limitation"].outer_years == 20
    assert by["labor.arbitration"].outer_years == 0
    assert by["civil.enforce"].outer_years == 0
    assert by["construction.priority"].outer_years == 0


def test_fatal_deadline_cannot_be_suspended_either(session, firm):
    """除斥期间既不中断也不中止。"""
    rows = service.generate(session, firm["p"], firm["case"].id, anchors={},
                            role="contractor", today=date(2026, 3, 1))
    fat = next(r for r in rows if r.fatal)
    with pytest.raises(DomainError) as e:
        service.suspend(session, firm["p"], fat.id, date(2026, 3, 1), "不可抗力")
    assert e.value.code == "task.not_suspendable"


def test_suspend_requires_evidence_note(session, firm):
    """中止同样要写明障碍事由 —— 事后要拿得出来。"""
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2024-01-10"},
                     role="contractor", today=date(2025, 1, 1))
    row = next(r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
               if r.rule_code == "construction.limitation")
    with pytest.raises(DomainError) as e:
        service.suspend(session, firm["p"], row.id, date(2025, 1, 1), "  ")
    assert e.value.code == "task.suspend_note_required"


def test_seed_backfills_new_builtin_fields_without_overwriting_edits(session, firm):
    """★ 内置规则新增字段要能补进**已经灌过**的所。

    这是被一次真事故逼出来的：加了「最长保护期 20 年」之后，单元测试
    （干净库）全绿，已有律所的库里却还是 0 —— 新库对、老库错，且不报错。
    但补空归补空，律所自己改过的值一个字都不能动。
    """
    tid = firm["tenant"].id
    service.ensure_default_rules(session, tid)
    by = {r.code: r for r in service.list_rules(session, firm["p"])}

    # 模拟老库：字段停在默认值
    by["construction.limitation"].outer_years = 0
    # 模拟律所自己的选择：改成了 15
    by["civil.limitation"].outer_years = 15
    session.flush()

    service.ensure_default_rules(session, tid)
    after = {r.code: r for r in service.list_rules(session, firm["p"])}
    assert after["construction.limitation"].outer_years == 20, "停在默认值的没补上"
    assert after["civil.limitation"].outer_years == 15, "把律所改过的值覆盖了"


def test_expired_and_capped_still_says_interruption_is_pointless(session, firm):
    """★ 已过期时这句话更要紧，不是更次要。

    律师看到一条过期的诉讼时效，第一反应往往是「再发封催款函中断一下」。
    这条已经顶到 20 年上限，中断没有任何余地 —— 这时候不说，
    就是眼看着他去做无用功。
    """
    service.generate(session, firm["p"], firm["case"].id,
                     anchors={"payable_on": "2005-03-01"},
                     role="contractor", today=date(2006, 1, 1))
    row = next(r for r in service.deadlines_of(session, firm["p"], firm["case"].id)
               if r.rule_code == "construction.limitation")
    row = service.interrupt(session, firm["p"], row.id, date(2024, 3, 1),
                            "催款", today=date(2026, 6, 1))
    d = service.describe(row, today=date(2026, 6, 1))
    assert d["status"] == "expired"
    assert d["capped"] is True
    assert "再发生中断也不会往后延" in d["message"]
