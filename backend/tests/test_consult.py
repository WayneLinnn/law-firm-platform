# -*- coding: utf-8 -*-
"""法律咨询。

★ **这个模块是 time（工时）拆掉之后剩下的那一点点。**

  原来那套是英美计时制律所的做法：人人每天记满 8 小时、按计费率换算、
  逐级审批、月底出账。1752 行，三张表，还带漏记提醒和取整口径。

  中国的提成制律所不这么运转 —— 团队成员和律师助理是工资制或分成制，
  他们的时间不换算成客户账单。硬套下去的结果是所有人应付式补录，
  **而假数据比没数据更糟：它会被拿去算成本、算人效、算分成。**

  真正按时间收钱的只有法律咨询，所以只留这一件。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from kernel.errors import DomainError
from modules.case import service as case_svc
from modules.consult import service as consult
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
    case_svc.ensure_default_causes(session, t.id)
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
    mate = person("li", "lawyer", "associate")

    party = crm_svc.create_party(session, t.id, "宏远建设", "org")
    code = crm_svc.next_client_code(session, t.id)
    client = crm_svc.create_client(session, t.id, party.id, code,
                                   owner_id=boss.id)

    return {"tenant": t, "org": team, "client": client,
            "boss": boss, "mate": mate,
            "p": access.build_principal(session, boss.id),
            "pm": access.build_principal(session, mate.id)}


def _log(session, firm, minutes=60, rate="1000", **kw):
    return consult.log(session, firm["p"], firm["client"].id,
                       kw.pop("on", date.today()), minutes,
                       hourly_rate=Decimal(rate), **kw)


# ================================================================ 没有工时那一套


def test_there_is_no_timekeeping_module_anymore(session, firm):
    """★ 工时模块整个拆掉了 —— 这条测试钉住它别回来。

    可拆卸不是口号：`time` 只被 billing 一处 `optional_api` 引用，
    所以删掉一个目录就拆干净了，别的模块一行没改。
    """
    from kernel.registry import registry

    names = set(registry.manifests)
    assert "time" not in names
    assert "consult" in names

    # 权限点里也不该再有 time.*
    perms = set()
    for m in registry.manifests.values():
        perms |= set(m.permissions)
    assert not [x for x in perms if x.startswith("time.")]


def test_no_approval_step(session, firm):
    """★ 咨询**没有审批环节**。

    一次咨询是已经发生的收费事实，不像工时那样需要
    "这 40 小时该不该报给客户"的判断。加一道审批只会让人拖着不记。
    """
    row = _log(session, firm, 60)
    assert row.status == "draft"
    # 记完直接就能进账单，不用先审
    assert consult.billable(session, firm["p"], firm["client"].id)


# ================================================================ 算钱


def test_amount_is_frozen_at_log_time(session, firm):
    """★ 金额**存下来，不每次重算**。费率会调，去年那条咨询不该跟着变。"""
    row = _log(session, firm, 60, rate="1200")
    assert row.amount == Decimal("1200.00")
    assert row.hourly_rate == Decimal("1200.00")


def test_rounds_up_to_half_an_hour(session, firm):
    """★ 咨询按**半小时**取整，不是工时那种 6 分钟制。

    6 分钟制是计时律所为了把每一分钟都换成钱设计的；
    咨询是"聊了一个多小时"这种粒度 ——
    报一个 1.3 小时的数字只会让客户觉得你在抠。
    """
    assert consult.billable_minutes(65) == 90
    assert consult.billable_minutes(90) == 90
    assert consult.billable_minutes(91) == 120

    row = _log(session, firm, 65, rate="1000")
    assert row.amount == Decimal("1500.00")     # 1.5 小时


def test_a_short_chat_is_not_billed(session, firm):
    """★ 不足 15 分钟不计费。

    见面寒暄二十分钟就开账单，客户不会有第二次。
    """
    assert consult.billable_minutes(10) == 0
    row = _log(session, firm, 10, rate="1000")
    assert row.amount == Decimal("0.00")


def test_money_rounds_half_up_not_bankers(session, firm):
    """★ 四舍五入，不是银行家舍入 —— 中国的发票和结算用的是前者。"""
    assert consult.money("0.125") == Decimal("0.13")
    assert consult.money("0.135") == Decimal("0.14")


# ================================================================ 免费咨询


def test_free_consult_needs_a_reason(session, firm):
    """★ 不写理由的免费，事后分不清是优惠还是漏收。"""
    with pytest.raises(DomainError) as e:
        _log(session, firm, 30, free=True)
    assert e.value.code == "consult.free_needs_reason"

    row = _log(session, firm, 30, free=True, free_reason="首次面谈")
    assert row.amount == Decimal("0.00")
    assert row.hourly_rate == Decimal("0.00")


def test_free_consult_never_becomes_billable(session, firm):
    """免费的不进账单 —— 这条规则归 consult 判，billing 不该知道。"""
    _log(session, firm, 60, free=True, free_reason="老客户")
    assert consult.billable(session, firm["p"], firm["client"].id) == []


# ================================================================ 挂谁


def test_client_is_required_case_is_not(session, firm):
    """★ 客户必填、案件选填。

    多数咨询发生在立案之前（甚至根本不会立案）——
    只能挂案件的话，"来聊一下、收 1500、没接案"这种最典型的情况记不进去。
    """
    row = _log(session, firm, 60)
    assert row.client_id == firm["client"].id
    assert row.case_id is None       # 没案子也记得下


def test_cannot_log_against_another_firms_client(session, firm):
    """跨所的客户挂不上去。"""
    other = identity.create_tenant(session, "other-x", "别家所")
    party = crm_svc.create_party(session, other.id, "别家的客户", "org")
    c = crm_svc.create_client(session, other.id, party.id, "KH2026-9999",
                              owner_id=firm["boss"].id)
    with pytest.raises(DomainError) as e:
        consult.log(session, firm["p"], c.id, date.today(), 60)
    assert e.value.code == "consult.cross_tenant"


# ================================================================ 改不动


def test_billed_consult_cannot_be_edited(session, firm):
    """★ 已经报给客户的数字，**不能事后悄悄改**。

    这是律所最经不起查的一类问题。要改只能作废重记。
    """
    row = _log(session, firm, 60)
    consult.mark_billed(session, firm["p"], [row.id], "FP2026-0001")
    session.refresh(row)

    with pytest.raises(DomainError) as e:
        consult.update(session, firm["p"], row.id, minutes=180)
    assert e.value.code == "consult.locked"
    assert "作废重记" in e.value.message


def test_voiding_needs_a_reason(session, firm):
    row = _log(session, firm, 60)
    with pytest.raises(DomainError) as e:
        consult.void(session, firm["p"], row.id, "  ")
    assert e.value.code == "consult.void_needs_reason"

    consult.void(session, firm["p"], row.id, "记重了")
    session.refresh(row)
    assert row.status == "void"


def test_billed_once_never_billed_twice(session, firm):
    """出过账的不会再被捞进第二张账单。"""
    row = _log(session, firm, 60)
    consult.mark_billed(session, firm["p"], [row.id], "FP2026-0001")
    assert consult.billable(session, firm["p"], firm["client"].id) == []
    # 再标一次也不会重复计数
    assert consult.mark_billed(session, firm["p"], [row.id], "FP2026-0002") == 0


# ================================================================ 输错防护


def test_an_absurd_duration_is_refused(session, firm):
    """一次咨询记了 10 小时 —— 多半是把分钟当成了别的单位。"""
    with pytest.raises(DomainError) as e:
        _log(session, firm, 600)
    assert e.value.code == "consult.too_long"
    # 报错要给出路
    assert "分成几条" in e.value.message


# ================================================================ 看得见谁的


def test_a_lawyer_only_sees_their_own(session, firm):
    """★ 咨询摘要里写着当事人来说了什么 ——
    那是隔离墙挡的东西，不能从咨询表这个后门漏出去。"""
    _log(session, firm, 60)                       # boss 记的
    mine = consult.list_consults(session, firm["pm"], )
    assert mine == []                             # 律师看不到别人的

    assert firm["pm"].can("consult.log")
    assert not firm["pm"].can("consult.view_all")


# ================================================================ 看板


def test_metric_is_separate_from_case_revenue(session, firm):
    """★ 咨询费和办案的钱**分开** —— 混在一起就看不出咨询这块值不值得做。"""
    _log(session, firm, 60, rate="1000")
    _log(session, firm, 120, rate="1000")
    out = consult.metrics(session, firm["p"],
                          {"since": date.today() - timedelta(days=7),
                           "until": date.today()})
    assert Decimal(out["consult.received"]["value"]) == Decimal("3000.00")


def test_voided_consult_is_not_counted(session, firm):
    row = _log(session, firm, 60, rate="1000")
    _log(session, firm, 60, rate="1000")
    consult.void(session, firm["p"], row.id, "记重了")
    out = consult.metrics(session, firm["p"],
                          {"since": date.today() - timedelta(days=7),
                           "until": date.today()})
    assert Decimal(out["consult.received"]["value"]) == Decimal("1000.00")


# ================================================================ 改名迁移


def test_renaming_a_permission_needs_a_migration(session, firm):
    """★ 拆模块时**权限点是存在库里的**，代码改了库不会跟着改。

    工时拆掉后，已经建好的租户角色上挂的还是 `time.log` 这些死代码，
    而新的 `consult.*` 谁都没有。结果不是报错，是**功能静默消失**：
    菜单不显示、接口 403，没有任何东西提示你少了点什么。

    ★ `ensure_default_roles` 帮不上忙 —— 它对已存在的角色整个跳过，
      刻意不补新权限（新模块上线该给谁，由律所决定不是代码决定）。
      那个设计是对的，但**改名不是新增**：
      老代码没了、新代码没发，中间掉下去的就是这个洞。

    这条测试钉住的是：默认角色装出来必须**真的能用**咨询功能。
    """
    from modules.identity import access as acc

    for code, expect in (("committee", True), ("team_leader", True),
                         ("lawyer", True), ("finance", False)):
        role = acc.get_role(session, firm["tenant"].id, code)
        perms = set(acc.role_permissions(session, role.id))
        # 死代码一个都不该有
        assert not [x for x in perms if x.startswith("time.")], code
        # 该看得到的看得到
        assert ("consult.view" in perms) is True, code
        # 记咨询是业务动作 —— 财务不该有
        assert ("consult.log" in perms) is expect, code


def test_consult_has_no_approval_permission_at_all(session, firm):
    """★ 改名时**不给不存在的东西找对应**。

    `time.approve` / `time.log_others` 在咨询里根本没有对应概念 ——
    咨询没有审批、没有代录。硬找一个新代码塞进去，
    等于凭空给人发了个他从来没有过的权限。
    """
    from kernel.registry import registry

    perms = set(registry.manifests["consult"].permissions)
    assert not [p for p in perms if "approve" in p or "others" in p]
    assert perms == {"consult.log", "consult.view",
                     "consult.view_all", "consult.manage_rate"}
