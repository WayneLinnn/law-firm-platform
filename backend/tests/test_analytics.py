# -*- coding: utf-8 -*-
"""Step 21 · 看板。

**围绕创收，不含工时。** 用户明确：不计工时，记创收 ——
计费率那套指标是英美计时制律所的，照搬过来只会得到没人看的数字。

重点测四件事：

  1. **各模块自报，analytics 只汇总** —— 加模块不用改 analytics
  2. **三个口径分得清** —— 开票 ≠ 回款 ≠ 未收
  3. **拿不到就说拿不到**，绝不当成 0
  4. **小样本不给分组** —— 3 个人的团队，知道两个就能反推第三个
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
    t1 = identity.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                  kind="team", parent_id=root.id)

    def person(name, role):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, "senior_partner")
        identity.assign_membership(session, u.id, t1.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    wang = person("wang", "committee")     # 全所
    li = person("li", "team_leader")       # 只到团队
    zhang = person("zhang", "lawyer")      # 只看自己

    return {"tenant": t, "org": t1, "causes": causes,
            "wang": wang, "li": li, "zhang": zhang,
            "p": access.build_principal(session, wang.id),
            "pl": access.build_principal(session, li.id),
            "pz": access.build_principal(session, zhang.id)}


def _case_with_revenue(session, firm, code, lead, amount, paid,
                       issued_ago=10, cause="construction"):
    """造一个有账单和回款的案子。"""
    today = date.today()
    c = case_svc.create_case(
        session, firm["tenant"].id, code, "案子" + code, lead_id=lead.id,
        cause_id=firm["causes"][cause].id, org_unit_id=firm["org"].id,
        opened_on=today - timedelta(days=issued_ago + 30))
    session.flush()
    party = crm_svc.create_party(session, firm["tenant"].id, "对方" + code,
                                 "org", allow_similar=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, party.id, "defendant")
    case_svc.transition(session, c.id, "conflict_check", actor_id=firm["wang"].id)
    chk = conflict.run_check(session, firm["tenant"].id, c.id,
                             requested_by=firm["wang"].id)
    if chk.status not in conflict.PASSING_STATUSES:
        conflict.decide(session, chk.id, "waived", firm["wang"].id, "测试放行")
    for to in ("pending", "active"):
        case_svc.transition(session, c.id, to, actor_id=firm["wang"].id)

    inv = bill.create_invoice(session, firm["p"], c.id,
                              due_on=today - timedelta(days=1))
    bill.add_line(session, firm["p"], inv.id, "法律服务费", amount)
    bill.issue(session, firm["p"], inv.id,
               issued_on=today - timedelta(days=issued_ago))
    if paid > 0:
        bill.record_payment(session, firm["p"], inv.id, Decimal(str(paid)),
                            paid_on=today - timedelta(days=1))
    return c, inv


# ================================================================ 汇总机制


def test_analytics_asks_modules_and_does_not_touch_their_tables(session, firm):
    """★ 各模块自报，analytics 只汇总。

    这是这个模块存在的全部理由：让 analytics 依赖案件、账单、时限、单据，
    加一个模块就要改一次 analytics；反过来 ——
    **加一个模块 = 看板自动多几个指标，analytics 一行不改。**
    """
    from kernel.registry import registry

    keys = {m.key for m in registry.metrics()}
    # 各模块都报上来了
    assert {"case.open", "billing.received", "task.expired",
            "approval.to_decide"} <= keys
    # ★ 一个工时指标都没有 —— 用户明确不计工时
    assert not [k for k in keys if k.startswith("time.")]


def test_catalog_hides_metrics_you_cannot_see(session, firm):
    """看不到的指标不该出现在目录里 —— 前端据此渲染，不写死。"""
    full = {m["key"] for m in an.catalog(firm["p"])}
    assert "billing.received" in full

    # 造一个没有 billing.view 的人
    p = firm["pz"]
    object.__setattr__(p, "permissions",
                       frozenset(x for x in p.permissions
                                 if not x.startswith("billing.")))
    lean = {m["key"] for m in an.catalog(p)}
    assert "billing.received" not in lean
    assert "case.open" in lean


def test_missing_module_is_declared_not_zeroed(session, firm):
    """★ 拿不到就说拿不到，**绝不当成 0**。

    一个模块挂了却显示「本月回款 ¥0」，会吓死人 —— 而且方向是反的：
    真实情况可能收得很好，只是那个模块没响应。
    """
    import modules.billing.api as bapi

    real = bapi.metrics
    try:
        def _boom(*a, **kw):
            raise RuntimeError("模块炸了")
        bapi.metrics = _boom
        out = an.overview(session, firm["p"], scope="firm")
        assert "billing" in out["missing"]
        # 没有假的 0 混进来
        assert "billing.received" not in out["values"]
    finally:
        bapi.metrics = real


# ================================================================ 创收三口径


def test_three_revenue_measures_are_distinct(session, firm):
    """★ 开票 ≠ 回款 ≠ 未收。混了就有人拿开票额去邀功。"""
    _case_with_revenue(session, firm, "R1", firm["wang"],
                       Decimal("100000"), 30000)
    out = an.overview(session, firm["p"], scope="firm",
                      since=date.today() - timedelta(days=90))
    v = out["values"]
    billed = Decimal(v["billing.billed"]["value"])
    received = Decimal(v["billing.received"]["value"])
    outstanding = Decimal(v["billing.outstanding"]["value"])

    assert billed > received                 # 开了 10 万，收了 3 万
    assert received == Decimal("30000.00")
    assert outstanding == billed - received


def test_received_counts_by_payment_date_not_invoice_date(session, firm):
    """★ 回款按**收款日**算，不按账单日。

    三月开的单五月收到，那是五月的创收 —— 按账单日算会把它记进三月，
    然后五月的报表上凭空少一笔。
    """
    _case_with_revenue(session, firm, "R2", firm["wang"],
                       Decimal("50000"), 50000, issued_ago=60)
    # 只看最近 7 天：账单是 60 天前开的，但钱是昨天到的
    out = an.overview(session, firm["p"], scope="firm",
                      since=date.today() - timedelta(days=7))
    v = out["values"]
    assert Decimal(v["billing.received"]["value"]) == Decimal("50000.00")
    # 开票额不在这个窗口里
    assert Decimal(v["billing.billed"]["value"]) == Decimal("0.00")


def test_overdue_is_measured_against_due_date(session, firm):
    """已逾期 = 过了约定付款日还没收到的。"""
    _case_with_revenue(session, firm, "R3", firm["wang"],
                       Decimal("80000"), 0)
    out = an.overview(session, firm["p"], scope="firm",
                      since=date.today() - timedelta(days=90))
    assert Decimal(out["values"]["billing.overdue"]["value"]) > 0


# ================================================================ 范围与权限


def test_mine_scope_only_counts_my_cases(session, firm):
    """「我的」只算我主办的。"""
    _case_with_revenue(session, firm, "M1", firm["wang"],
                       Decimal("10000"), 10000)
    _case_with_revenue(session, firm, "M2", firm["li"],
                       Decimal("90000"), 90000)

    mine = an.overview(session, firm["p"], scope="mine",
                       since=date.today() - timedelta(days=90))
    allof = an.overview(session, firm["p"], scope="firm",
                        since=date.today() - timedelta(days=90))
    assert Decimal(mine["values"]["billing.received"]["value"]) == Decimal("10000.00")
    assert Decimal(allof["values"]["billing.received"]["value"]) == Decimal("100000.00")


def test_team_leader_cannot_see_firm_board(session, firm):
    """★ 全所创收是管委会的事 —— 不是每个团队都该看到别的团队挣多少。"""
    with pytest.raises(DomainError) as e:
        an.overview(session, firm["pl"], scope="firm")
    assert e.value.code == "analytics.forbidden"
    # 但团队和自己的看得到
    assert an.overview(session, firm["pl"], scope="mine")


def test_team_scope_needs_an_org(session, firm):
    with pytest.raises(DomainError) as e:
        an.overview(session, firm["pl"], scope="team")
    assert e.value.code == "analytics.org_required"


# ================================================================ 小样本保护


def test_small_team_is_not_broken_down_by_person(session, firm):
    """★ 3 个人以内不给分组。

    团队 3 个人、总额已知，知道另外两个就能反推第三个 ——
    **聚合数字看着无害，实际能反推到个人。**
    """
    _case_with_revenue(session, firm, "S1", firm["wang"],
                       Decimal("10000"), 10000)
    _case_with_revenue(session, firm, "S2", firm["li"],
                       Decimal("20000"), 20000)

    out = an.overview(session, firm["p"], scope="firm",
                      since=date.today() - timedelta(days=90))
    bl = out["by_lead"]
    assert bl["suppressed"] is True
    assert bl["rows"] == []
    # ★ 要说清为什么压制，不是默默给个空表
    assert "反推" in bl["reason"]
    # 总数还是给的
    assert Decimal(bl["total"]["received"]) == Decimal("30000.00")


def test_three_or_more_people_get_a_breakdown(session, firm):
    """人够多就可以拆开了。"""
    _case_with_revenue(session, firm, "T1", firm["wang"], Decimal("10000"), 10000)
    _case_with_revenue(session, firm, "T2", firm["li"], Decimal("20000"), 20000)
    _case_with_revenue(session, firm, "T3", firm["zhang"], Decimal("30000"), 30000)

    out = an.overview(session, firm["p"], scope="firm",
                      since=date.today() - timedelta(days=90))
    bl = out["by_lead"]
    assert bl["suppressed"] is False
    assert len(bl["rows"]) == 3
    # 按回款倒序
    got = [Decimal(r["received"]) for r in bl["rows"]]
    assert got == sorted(got, reverse=True)


# ================================================================ 风险清单


def test_at_risk_lists_what_cannot_be_collected(session, firm):
    """★ 这一栏是看板最该看的东西 —— 风险不是"没开单"，是"开了收不回来"。"""
    _case_with_revenue(session, firm, "A1", firm["wang"], Decimal("10000"), 10000)
    c2, _ = _case_with_revenue(session, firm, "A2", firm["wang"],
                               Decimal("200000"), 0)

    out = an.overview(session, firm["p"], scope="firm",
                      since=date.today() - timedelta(days=90))
    risk = out["at_risk"]
    # 收齐的那个不该在里面
    assert all(Decimal(r["outstanding"]) > 0 for r in risk)
    # 欠得最多的排最前
    assert risk[0]["case_id"] == c2.id


# ================================================================ 口径默认


def test_default_range_is_this_month_not_last_30_days(session, firm):
    """★ 律所按**月**结算。「最近 30 天」跨两个结算月，对不上任何月报表。"""
    r = an.month_range(date(2026, 8, 28))
    assert r["since"] == date(2026, 8, 1)
    assert r["until"] == date(2026, 8, 28)


# ================================================================ 统一待办中心


def test_inbox_asks_modules_and_does_not_touch_their_tables(session, firm):
    """★ 第五次用「各模块自报，汇总层只汇总」。

    待办原来散在三个地方 —— 待办页有时限、单据页有「待我处理」、
    工作台还有一排格子。律师早上要点三个地方才知道今天有什么会出事，
    **于是就不点了**。
    """
    from kernel.registry import registry

    mods = registry.inbox_modules()
    assert "task" in mods and "approval" in mods

    out = an.inbox(session, firm["p"])
    assert set(out["asked"]) == set(mods)
    assert out["missing"] == []


def test_inbox_does_not_leak_past_a_module_permission_gate(session, firm):
    """★★ **聚合层不该替被聚合的模块判断授权。**

    这条测试钉住一个真出过的漏洞：统一待办中心直接调 `task.board()`，
    而 board 本身不查权限（查权限的是路由）——
    于是**财务（没有 task.view）在待办中心里看到了案件时限**。

    谁能看什么是各模块自己的事。一旦交给汇总层去判，
    汇总层就要知道每个模块的权限模型，而它迟早会漏掉一个。
    """
    p = firm["pz"]
    # 拿掉 task.view，模拟财务
    object.__setattr__(p, "permissions",
                       frozenset(x for x in p.permissions
                                 if not x.startswith("task.")))
    assert not p.can("task.view")

    out = an.inbox(session, p)
    assert not [i for i in out["items"] if i["kind"] in ("deadline", "task")]


def test_inbox_sorts_unknown_before_urgent(session, firm):
    """★ **「算不出」排在「临近」前面** —— 顺序本身是个业务判断。

    临近的期限至少系统在替你盯着；算不出的那些**没人在盯**，
    它缺起算日，谁也不知道还剩几天。
    """
    order = an._URGENCY_ORDER
    assert order["expired"] < order["unknown"] < order["urgent"] < order["normal"]


def test_inbox_item_always_has_somewhere_to_go(session, firm):
    """★ 没有 link 的待办等于没有 —— 人看到了却不知道去哪做。"""
    out = an.inbox(session, firm["p"])
    for it in out["items"]:
        assert it["link"], it
        assert it["title"], it
        # ★ 「为什么是我」必须说清 —— 单据是按权限算的，
        #   不说的话团队里每个人都以为是别人的事
        assert it["why_me"], it


def test_a_dead_module_is_declared_not_silently_dropped(session, firm):
    """★ 某个模块炸了要**明说**，不能当成"那边没事"。

    一个漏掉了单据的待办列表，比没有列表更危险 ——
    它让人以为自己已经看全了。
    """
    import modules.approval.api as aapi

    real = aapi.inbox_items
    try:
        def _boom(*a, **kw):
            raise RuntimeError("模块炸了")
        aapi.inbox_items = _boom
        out = an.inbox(session, firm["p"])
        assert "approval" in out["missing"]
    finally:
        aapi.inbox_items = real
