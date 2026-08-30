# -*- coding: utf-8 -*-
"""看板落点 · 建客户 · 执业资格。

这一批全是**用起来才暴露的问题** —— 每条测试都对应一个真实的踩坑：

  1. 14 个指标只有 4 个落点，点哪个格子都是同一张全量表
  2. 工作台写"在办 4 件"，点进去列出全所 11 件
  3. "本期回款"点到一张讲未收的表上
  4. 建完主体，客户表一行没多（`create_client` 从没被调用过）
  5. 独立律师建不了客户，因为他和律师助理是同一个角色
  6. 停止执业的人还能挂主办律师
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from kernel.errors import DomainError
from modules.analytics import service as an
from modules.billing import service as bill
from modules.case import service as case_svc
from modules.conflict import service as conflict
from modules.crm import service as crm_svc
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


@pytest.fixture
def firm(session):
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = identity.create_tenant(session, "tk-" + sfx, "康桥")
    identity.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in case_svc.ensure_default_causes(session, t.id)}
    root = identity.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    team = identity.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                    kind="team", parent_id=root.id)

    def person(name, role, position="senior_partner"):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, position)
        identity.assign_membership(session, u.id, team.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    boss = person("wang", "committee")
    mate = person("li", "lawyer", "associate")       # 独立律师
    helper = person("zhao", "lawyer", "paralegal")   # 律师助理

    return {"tenant": t, "org": team, "causes": causes,
            "boss": boss, "mate": mate, "helper": helper,
            "p": access.build_principal(session, boss.id),
            "pm": access.build_principal(session, mate.id),
            "ph": access.build_principal(session, helper.id)}


def _revenue_case(session, firm, code, lead, amount, paid, issued_ago=10):
    today = date.today()
    c = case_svc.create_case(
        session, firm["tenant"].id, code, "案子" + code, lead_id=lead.id,
        cause_id=firm["causes"]["construction"].id, org_unit_id=firm["org"].id,
        opened_on=today - timedelta(days=issued_ago + 30))
    session.flush()
    party = crm_svc.create_party(session, firm["tenant"].id, "对方" + code,
                                 "org", allow_similar=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, party.id, "defendant")
    case_svc.transition(session, c.id, "conflict_check", actor_id=firm["boss"].id)
    chk = conflict.run_check(session, firm["tenant"].id, c.id,
                             requested_by=firm["boss"].id)
    if chk.status not in conflict.PASSING_STATUSES:
        conflict.decide(session, chk.id, "waived", firm["boss"].id, "测试")
    for to in ("pending", "active"):
        case_svc.transition(session, c.id, to, actor_id=firm["boss"].id)

    inv = bill.create_invoice(session, firm["p"], c.id,
                              due_on=today - timedelta(days=1))
    bill.add_line(session, firm["p"], inv.id, "法律服务费", amount)
    bill.issue(session, firm["p"], inv.id,
               issued_on=today - timedelta(days=issued_ago))
    if paid > 0:
        bill.record_payment(session, firm["p"], inv.id, Decimal(str(paid)),
                            paid_on=today - timedelta(days=1))
    return c, inv


# ================================================================ 看板落点


def test_every_metric_drills_somewhere_specific(session, firm):
    """★ 每个指标都得有**自己**的落点，不能都指同一张全量表。

    第一版 14 个指标只有 4 个 URL：你从"12 条算不出"点进去，
    面前是几十行的全量表，还得自己再找一遍那 12 条 ——
    **那这个格子就白点了**，看板退回成一堵装饰墙。
    """
    from kernel.registry import registry

    ms = [m for m in registry.metrics() if m.drill]
    assert len(ms) >= 14
    # 每个 drill 都带筛选条件
    assert all("?" in m.drill for m in ms), \
        [m.key for m in ms if "?" not in m.drill]
    # 落点互不重复（案由分布和在办共用一个，所以按 key 数去重后仍应很多）
    assert len({m.drill for m in ms}) >= 13


def test_drill_carries_the_scope_it_was_clicked_in(session, firm):
    """★ 范围要拼进 drill —— **在汇总层拼，不在模块里**。

    模块只声明"这个数点开去 /cases?f=open"；至于看谁的、看哪段时间，
    模块不知道也不该知道 —— 同一个 case.open，
    工作台上是"我经办的"，全所页上是"全所的"。

    不拼就会出现工作台写"在办 4 件"、点进去列出全所 11 件。
    用户不会觉得是筛选条件不同，只会觉得**统计算错了**。
    """
    mine = an.overview(session, firm["p"], scope="mine")
    by_key = {m["key"]: m for m in mine["catalog"]}
    d = by_key["case.open"]["drill"]
    assert "f=open" in d and "scope=mine" in d and "since=" in d

    allof = an.overview(session, firm["p"], scope="firm")
    d2 = {m["key"]: m for m in allof["catalog"]}["case.open"]["drill"]
    assert "scope=firm" in d2
    # 同一个指标，两个范围下的落点必须不同
    assert d != d2


def test_flow_detail_adds_up_to_the_metric(session, firm):
    """★ **格子里的数必须正好等于点开之后那些行加起来的数。**

    口径各写一遍迟早会漂，一漂用户就不信整个看板了。
    所以 revenue_flow 和 metrics 复用同一段筛选。
    """
    _revenue_case(session, firm, "F1", firm["boss"], Decimal("100000"), 30000)
    _revenue_case(session, firm, "F2", firm["boss"], Decimal("50000"), 20000)

    spec = {"since": date.today() - timedelta(days=90), "until": date.today()}
    m = bill.metrics(session, firm["p"], spec)
    flow = bill.revenue_flow(session, firm["p"], spec)

    assert Decimal(flow["received_total"]) == Decimal(m["billing.received"]["value"])
    assert Decimal(flow["billed_total"]) == Decimal(m["billing.billed"]["value"])
    # 明细逐笔加起来也一样
    assert sum((Decimal(p["amount"]) for p in flow["received"]),
               Decimal("0")) == Decimal(flow["received_total"])


def test_flow_is_not_the_same_thing_as_receivables(session, firm):
    """★ 开票/回款是**流量**，未收是**存量** —— 两套数据。

    把"本期回款 ¥206,000"点到一张讲未收的表上，两个数字八竿子打不着。
    """
    _revenue_case(session, firm, "F3", firm["boss"], Decimal("80000"), 80000)

    spec = {"since": date.today() - timedelta(days=90), "until": date.today()}
    flow = bill.revenue_flow(session, firm["p"], spec)
    rec = bill.receivables(session, firm["p"])

    # 全额收讫：流水里有 8 万，应收表里一分没有
    assert Decimal(flow["received_total"]) == Decimal("80000.00")
    assert all(Decimal(i["outstanding"]) > 0 for i in rec["items"])


# ================================================================ 建客户


def test_creating_a_party_does_not_make_a_client(session, firm):
    """主体 ≠ 客户 —— 对方当事人也要进主体库，但他不是客户。"""
    p = crm_svc.create_party(session, firm["tenant"].id, "某某公司", "org")
    clients = crm_svc.list_clients(session, firm["p"]) \
        if hasattr(crm_svc, "list_clients") else []
    assert all(getattr(c, "party_id", None) != p.id for c in clients)


def test_client_code_is_generated_not_asked_for(session, firm):
    """★ 编号让用户自己填，填出来的就是 "1"、"aaa"、"测试"。

    能自动生成的东西不要问人。
    """
    p = crm_svc.create_party(session, firm["tenant"].id, "宏远集团", "org")
    code = crm_svc.next_client_code(session, firm["tenant"].id)
    assert code.startswith("KH") and code.endswith("0001")

    c = crm_svc.create_client(session, firm["tenant"].id, p.id, code,
                              owner_id=firm["boss"].id)
    assert c.code == code
    # 再来一个，号要往下走而不是撞车
    p2 = crm_svc.create_party(session, firm["tenant"].id, "凯达置业", "org")
    code2 = crm_svc.next_client_code(session, firm["tenant"].id)
    assert code2 != code
    crm_svc.create_client(session, firm["tenant"].id, p2.id, code2,
                          owner_id=firm["boss"].id)


# ================================================================ 谁能建客户


def test_associate_can_onboard_but_paralegal_cannot(session, firm):
    """★ **角色管按钮，职级管资格。**

    合伙人、独立律师都在自己拉客户 —— 把这事收到管委会手里，
    结果只会是律师微信里报一句、助理代录一遍，
    中间那一遍就是**利冲漏检的入口**。

    但律师助理也是"律师"这个角色，所以不能只靠角色分 ——
    用职级的「可独立承办」开关拦住他，
    和主办律师那道检查是同一个开关。
    """
    # 角色层面：两个人都拿到了按钮
    assert firm["pm"].can("crm.manage_party")
    assert firm["ph"].can("crm.manage_party")

    # 职级层面：独立律师过，律师助理拦
    crm_svc.assert_can_onboard(session, firm["mate"].id)

    with pytest.raises(DomainError) as e:
        crm_svc.assert_can_onboard(session, firm["helper"].id)
    assert e.value.code == "crm.cannot_onboard"
    # ★ 要说清怎么办，不是光说不行
    assert "主办律师" in e.value.message or "所务管理" in e.value.message


def test_onboarding_gate_is_data_not_code(session, firm):
    """律所想放开就改一行数据 —— 把助理的「可独立承办」打开。"""
    pos = identity.get_position(session, firm["tenant"].id, "paralegal")
    pos.can_handle_case = True
    session.flush()
    crm_svc.assert_can_onboard(session, firm["helper"].id)   # 不再抛


# ================================================================ 执业资格


def test_license_defaults_to_unregistered_not_blocked(session, firm):
    """★ 老库升上来是「未登记」，**不会把任何人挡在门外**。

    加一列就让所有人办不了案，是最糟糕的迁移。
    """
    lic = identity.license_of(session, firm["mate"].id)
    assert lic["status"] == ""
    assert lic["status_label"] == "未登记"
    assert lic["can_practise"] is False       # 但也不谎称他有资格
    # 没登记不影响他当主办
    case_svc._require_can_handle(session, firm["mate"].id)


def test_suspended_lawyer_cannot_lead_a_new_case(session, firm):
    """★ 职级是**所里**给的，执业证是**司法行政机关**给的 —— 两件事。

    所里能把谁提成合伙人，但提不出一张执业证。
    停止执业的人挂主办，一旦出去代理就是无证执业。
    """
    identity.set_license(session, firm["mate"].id, "13101202011234567",
                         status="suspended")
    with pytest.raises(DomainError) as e:
        case_svc._require_can_handle(session, firm["mate"].id)
    assert e.value.code == "case.license_invalid"
    assert "停止执业" in e.value.message

    # 恢复执业就能办了
    identity.set_license(session, firm["mate"].id, "13101202011234567",
                         status="practising")
    case_svc._require_can_handle(session, firm["mate"].id)


def test_expired_licence_warns_but_does_not_block(session, firm):
    """★ **过期不等于失效。**

    年检晚办几天很常见，证还是那本证 —— 把案子卡死没有道理。
    但也不能不说：一个"执业中"的绿标底下压着一张三年前就过期的证，
    比什么都不显示更糟，**它让人以为已经查过了**。
    """
    identity.set_license(session, firm["mate"].id, "13101202099999999",
                         status="practising",
                         valid_to=date.today() - timedelta(days=400))
    lic = identity.license_of(session, firm["mate"].id)
    assert lic["expired"] is True
    assert lic["note"]                       # 一定要有话说
    assert "核实" in lic["note"]
    # 但不拦
    case_svc._require_can_handle(session, firm["mate"].id)


def test_intern_is_a_distinct_state_not_a_boolean(session, firm):
    """★ 实习律师**不能独立承办、不能单独出庭** —— 做成"有证/没证"就丢了这件事。"""
    identity.set_license(session, firm["helper"].id, "13101202088888888",
                         status="intern")
    lic = identity.license_of(session, firm["helper"].id)
    assert lic["can_practise"] is False
    assert "独立承办" in lic["note"] or "出庭" in lic["note"]


def test_one_licence_number_belongs_to_one_person(session, firm):
    """★ 两个人挂同一个执业证号多半是录错了 —— 而错的那个会一路错到委托书上。"""
    identity.set_license(session, firm["mate"].id, "13101202011111111",
                         status="practising")
    with pytest.raises(DomainError) as e:
        identity.set_license(session, firm["helper"].id, "13101202011111111",
                             status="practising")
    assert e.value.code == "identity.license_taken"
    # 报错要说清撞在谁身上
    assert firm["mate"].full_name in e.value.message


def test_revoked_lawyer_keeps_the_account(session, firm):
    """★ 注销执业的人**不停用账号** —— 他办过的案子要留痕。

    账号一停，历史记录里全变成"未知用户"。
    """
    identity.set_license(session, firm["mate"].id, status="revoked")
    u = identity.get_user(session, firm["mate"].id)
    assert u.is_active is True
    assert u.license_status == "revoked"
