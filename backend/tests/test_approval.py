# -*- coding: utf-8 -*-
"""Step 18 · 单据流转。

**头号原则：财务只看得到单据和金额，看不到案情。**

手册给这一步标的坑就是这句话，也是这个模块存在的主要理由 ——
不然一张请款单用邮件发发也就完了。

所以重点测四件事：

  1. ★ **隔离墙** —— payload 里没有案情；事由里写了案件名会被拦
  2. **链条固化** —— 提交那一刻定死，规则后来改了也不影响
  3. **分权** —— 批的人和付的人不是同一个；不能批自己的单
  4. **回写账务** —— 钱付出去，代垫自动挂到案子上等着开票
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from kernel.errors import DomainError, NotFound
from modules.approval import service
from modules.billing import service as billing_svc
from modules.case import service as case_svc
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
    t2 = identity.create_org_unit(session, t.id, "别人团队", "t2-" + sfx,
                                  kind="team", parent_id=root.id)

    def person(name, role, org=None, position="senior_partner"):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, position)
        identity.assign_membership(session, u.id, (org or t1).id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    wang = person("wang", "committee")       # 管委会：所级审批
    li = person("li", "team_leader")         # 团队负责人：团队审批
    zhang = person("zhang", "lawyer")        # 律师：提单
    qian = person("qian", "finance")         # 财务：只付款
    sun = person("sun", "staff")             # 行政：用印执行

    case = case_svc.create_case(
        session, t.id, "AJ" + sfx[:6], "宏远建设诉凯达置业工程款",
        lead_id=zhang.id, cause_id=causes["construction"].id, org_unit_id=t1.id)
    party = crm_svc.create_party(session, t.id, "凯达置业集团" + sfx[:4], "org",
                                 allow_similar=True)
    case_svc.add_party(session, t.id, case.id, party.id, "defendant")

    service.ensure_default_flows(session, t.id)
    billing_svc.set_rate(session, access.build_principal(session, wang.id),
                         Decimal("3000"), position_code="senior_partner",
                         valid_from=date(2020, 1, 1))

    return {
        "tenant": t, "case": case, "party": party,
        "wang": wang, "li": li, "zhang": zhang, "qian": qian, "sun": sun,
        "p": access.build_principal(session, wang.id),
        "pl": access.build_principal(session, li.id),
        "pz": access.build_principal(session, zhang.id),
        "pq": access.build_principal(session, qian.id),
        "ps": access.build_principal(session, sun.id),
    }


def _ticket(session, firm, **kw):
    kw.setdefault("kind", "payment")
    kw.setdefault("subject", "诉讼费垫付")
    kw.setdefault("amount", Decimal("2000"))
    kw.setdefault("case_id", firm["case"].id)
    kw.setdefault("payee", "某某市中级人民法院")
    p = kw.pop("principal", firm["pz"])
    return service.create_ticket(session, p, **kw)


# ================================================================ ★ 隔离墙


def test_finance_payload_has_no_case_facts(session, firm):
    """★ 这是整个模块存在的理由。

    财务要付这笔钱，需要知道的是：多少钱、付给谁、什么名目、谁批的。
    **不需要**知道这是哪个当事人、什么纠纷 ——
    而律所里最敏感的东西恰恰是这些。
    """
    t = _ticket(session, firm)
    out = service.describe(session, firm["pq"], t)

    # 单据本身就没有案情字段（结构隔离）
    blob = str(out)
    assert firm["case"].title not in blob
    assert firm["party"].name not in blob
    # 财务要付款，看得到金额、收款方、事由
    assert out["amount"] == "2000.00"
    assert out["payee"] == "某某市中级人民法院"
    assert out["subject"] == "诉讼费垫付"


def test_invisible_case_is_declared_not_silently_hidden(session, firm):
    """★ 看不到的案件要**明说**，不能悄悄抹成 null。

    悄悄抹掉的后果：财务以为这单没挂案子，于是在备注里问"这是哪个案子的"，
    律师在备注里答了 —— 隔离墙就从备注栏漏光了。
    **明说看不到，人才不会去猜、去问、去补。**
    """
    # 造一个财务看不见的案子：换个团队、换个主办
    other = case_svc.create_case(
        session, firm["tenant"].id, "MI" + str(firm["case"].id),
        "别人的案子", lead_id=firm["li"].id)
    t = _ticket(session, firm, case_id=None)
    t.case_id = other.id
    session.flush()

    # 律师（本案主办）看不到别人的案子
    out = service.describe(session, firm["pz"], t)
    assert out["case_visible"] is False
    assert out["case_id"] is None
    assert "没有该案件的查看权限" in out["case_note"]
    # ★ 要写清"这不是错误"，否则人会当成 bug 去追
    assert "不是错误" in out["case_note"]


def test_case_name_in_subject_is_blocked(session, firm):
    """★ 事由这一栏太顺手了。

    ticket 上没有案情字段，但事由是自由文本，而它会跟着单子
    在所里流转 —— 财务、行政、用印管理都看得到。
    律师顺手写成「宏远建设诉凯达置业工程款的鉴定费」，隔离墙就白建了。
    """
    with pytest.raises(DomainError) as e:
        _ticket(session, firm, subject=firm["case"].title + "的鉴定费")
    assert e.value.code == "approval.subject_leaks_case"
    # 报错要给**改法**，不能只说"不行"
    assert "诉讼费垫付" in str(e.value) or "改成" in str(e.value)


def test_party_name_in_subject_is_blocked(session, firm):
    """当事人名字同样拦 —— 它比案件标题更敏感。"""
    with pytest.raises(DomainError) as e:
        _ticket(session, firm, subject="给" + firm["party"].name + "的保全担保费")
    assert e.value.code == "approval.subject_leaks_case"
    assert firm["party"].name in str(e.value)


def test_leak_check_also_covers_the_detail_field(session, firm):
    """补充说明和事由一起跟着单子走 —— 只拦事由等于没拦。"""
    with pytest.raises(DomainError) as e:
        _ticket(session, firm, subject="鉴定费垫付",
                detail="本案是" + firm["case"].title + "，需要造价鉴定")
    assert e.value.code == "approval.subject_leaks_case"


def test_a_clean_subject_passes(session, firm):
    """不透露案情的写法要放行 —— 拦得太宽人就绕着走了。"""
    t = _ticket(session, firm, subject="诉讼费垫付", detail="法院缴费通知已上传")
    assert t.id


def test_short_names_do_not_false_positive(session, firm):
    """★ 太短的名字不当特征 —— 误伤比漏拦更让人烦。

    「离婚」两个字做案件标题，会把"离婚"这个词从所有单据里赶走。
    """
    tiny = case_svc.create_case(session, firm["tenant"].id,
                                "SHORT" + str(firm["case"].id), "离婚",
                                lead_id=firm["zhang"].id)
    t = _ticket(session, firm, case_id=tiny.id, subject="离婚案件诉讼费垫付")
    assert t.id


def test_account_is_masked_for_people_who_do_not_pay(session, firm):
    """收款账号只给要付款的人看完整的。"""
    t = _ticket(session, firm, payee_account="6222021234567890123")
    lawyer_view = service.describe(session, firm["pz"], t)
    assert lawyer_view["payee_account"].endswith("0123")
    assert "*" in lawyer_view["payee_account"]
    assert lawyer_view["account_masked"] is True

    finance_view = service.describe(session, firm["pq"], t)
    assert finance_view["payee_account"] == "6222021234567890123"


# ================================================================ 流转链


def test_flow_is_chosen_by_amount(session, firm):
    """五千以下一道，五千以上两道 —— 门槛是所里定的，做成数据。"""
    small = service.flow_for(session, firm["tenant"].id, "payment",
                             Decimal("2000"))
    big = service.flow_for(session, firm["tenant"].id, "payment",
                           Decimal("8000"))
    assert len(service.parse_steps(small.steps)) == 1
    assert len(service.parse_steps(big.steps)) == 2


def test_no_flow_rule_refuses_instead_of_falling_back(session, firm):
    """★ 找不到规则就报错，不给"谁都能批"兜底。

    兜底链看着体贴，实际是把一张没人配过规则的单子悄悄放行了 ——
    而钱一旦付出去，没人会回头问"这单当初是按哪条规则批的"。
    """
    for r in service.list_flows(session, firm["p"], "payment"):
        r.active = False
    session.flush()
    t = _ticket(session, firm)
    with pytest.raises(DomainError) as e:
        service.submit(session, firm["pz"], t.id)
    assert e.value.code == "approval.no_flow_rule"
    assert "配一条流转规则" in str(e.value)


def test_chain_is_frozen_at_submit(session, firm):
    """★ 链条在提交那一刻定死。

    规则后来改了（门槛从五千提到一万），已经在走的单子不跟着变 ——
    否则一张走到一半的单子会突然多出或少掉一道，
    而没人说得清它到底该几道。
    """
    t = _ticket(session, firm, amount=Decimal("8000"))
    service.submit(session, firm["pz"], t.id)
    assert len(service.steps_of(session, firm["pz"], t.id)) == 2

    # 把门槛改到一万，这张单不该受影响
    for r in service.list_flows(session, firm["p"], "payment"):
        if r.max_amount == Decimal("5000.00"):
            r.max_amount = Decimal("10000.00")
        elif r.min_amount == Decimal("5000.00"):
            r.min_amount = Decimal("10000.00")
    session.flush()
    assert len(service.steps_of(session, firm["pz"], t.id)) == 2


def test_two_step_chain_walks_in_order(session, firm):
    t = _ticket(session, firm, amount=Decimal("8000"))
    service.submit(session, firm["pz"], t.id)
    assert t.current_seq == 1

    service.decide(session, firm["pl"], t.id, approve=True)   # 团队负责人
    assert t.status == "pending" and t.current_seq == 2

    service.decide(session, firm["p"], t.id, approve=True)    # 管委会
    assert t.status == "approved"
    # ★ 走完不等于付完钱
    assert t.paid_on is None


# ================================================================ 分权


def test_cannot_approve_your_own_ticket(session, firm):
    """自己批自己等于没有审批。"""
    t = _ticket(session, firm, principal=firm["pl"], amount=Decimal("2000"))
    service.submit(session, firm["pl"], t.id)
    with pytest.raises(DomainError) as e:
        service.decide(session, firm["pl"], t.id, approve=True)
    assert e.value.code == "approval.self_approve"


def test_team_leader_cannot_do_the_firm_step(session, firm):
    """★ 五千以上要过管委会，正是为了让第二个人看一眼。

    把两道权限给同一个人，两道就变成了一道。
    """
    t = _ticket(session, firm, amount=Decimal("8000"))
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    with pytest.raises(DomainError) as e:
        service.decide(session, firm["pl"], t.id, approve=True)
    assert e.value.code == "approval.forbidden"
    assert "approval.approve_firm" in str(e.value)


def test_finance_cannot_approve_only_pay(session, firm):
    """★ 批的是"这钱该不该花"，付的是"钱确实出去了"。

    同一个人既批又付，是所有财务内控的第一条红线。
    """
    t = _ticket(session, firm)
    service.submit(session, firm["pz"], t.id)
    with pytest.raises(DomainError) as e:
        service.decide(session, firm["pq"], t.id, approve=True)
    assert e.value.code == "approval.forbidden"

    service.decide(session, firm["pl"], t.id, approve=True)
    service.pay(session, firm["pq"], t.id, reference="BANK-001")   # 财务能付
    assert t.status == "paid"


def test_lawyer_cannot_approve_anything(session, firm):
    t = _ticket(session, firm, principal=firm["pl"])
    service.submit(session, firm["pl"], t.id)
    with pytest.raises(DomainError) as e:
        service.decide(session, firm["pz"], t.id, approve=True)
    assert e.value.code == "approval.forbidden"


def test_rejection_must_say_why(session, firm):
    """★ 不说为什么，申请人只会原样再提一次。"""
    t = _ticket(session, firm)
    service.submit(session, firm["pz"], t.id)
    with pytest.raises(DomainError) as e:
        service.decide(session, firm["pl"], t.id, approve=False, note="  ")
    assert e.value.code == "approval.reject_note_required"

    service.decide(session, firm["pl"], t.id, approve=False,
                   note="缴费通知没上传，补了再提")
    assert t.status == "rejected"
    assert "缴费通知" in t.close_note


def test_pay_only_after_approved(session, firm):
    t = _ticket(session, firm)
    service.submit(session, firm["pz"], t.id)
    with pytest.raises(DomainError) as e:
        service.pay(session, firm["pq"], t.id)
    assert e.value.code == "approval.bad_status"


def test_inbox_is_by_permission_not_assignment(session, firm):
    """★ 按权限算不按指派 —— 负责人出差三天不该让所有单子停摆。"""
    t = _ticket(session, firm)
    service.submit(session, firm["pz"], t.id)

    assert any(x.id == t.id for x in service.inbox(session, firm["pl"]))
    # 提单人自己不在待办里
    assert not any(x.id == t.id for x in service.inbox(session, firm["pz"]))
    # 财务这会儿还看不到（还没批完）
    assert not any(x.id == t.id for x in service.inbox(session, firm["pq"]))

    service.decide(session, firm["pl"], t.id, approve=True)
    # 批完了，轮到财务
    assert any(x.id == t.id for x in service.inbox(session, firm["pq"]))


# ================================================================ 回写账务


def test_paying_writes_back_a_disbursement(session, firm):
    """★ 钱一付出去，代垫就挂在案子上等着开票。

    不这么做，垫钱和开账单差着好几个月，全靠人记得 ——
    记漏了律所自己贴，而且和漏记工时一样，没人会发现。
    """
    t = _ticket(session, firm, amount=Decimal("2000"), subject="诉讼费垫付")
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    service.pay(session, firm["pq"], t.id, reference="BANK-002")

    assert t.disbursement_id is not None
    pend = billing_svc.pending_disbursements(session, firm["p"], firm["case"].id)
    assert len(pend) == 1
    assert pend[0].amount == Decimal("2000.00")
    assert pend[0].description == "诉讼费垫付"
    assert pend[0].ticket_id == t.id


def test_disbursement_flows_into_the_invoice(session, firm):
    """整条链走通：请款 → 审批 → 付款 → 代垫 → 账单。"""
    t = _ticket(session, firm, amount=Decimal("2000"), subject="诉讼费垫付")
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    service.pay(session, firm["pq"], t.id)

    inv = billing_svc.create_invoice(session, firm["p"], firm["case"].id)
    out = billing_svc.pull_disbursements(session, firm["p"], inv.id)
    assert out["pulled"] == 1
    session.refresh(inv)
    # ★ 代垫不计增值税
    assert inv.disbursement_amount == Decimal("2000.00")
    assert inv.tax_amount == Decimal("0.00")
    assert inv.total_amount == Decimal("2000.00")

    billing_svc.issue(session, firm["p"], inv.id)
    # 开票之后不会被再拉一次
    assert billing_svc.pending_disbursements(
        session, firm["p"], firm["case"].id) == []


def test_reimburse_does_not_write_back(session, firm):
    """★ 报销是所里的成本，不是垫给客户的钱 —— 不该挂到案子上开票。

    混在一起，客户会被收到律师的打车费。
    """
    t = _ticket(session, firm, kind="reimburse", subject="出差交通费",
                amount=Decimal("800"))
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    service.pay(session, firm["pq"], t.id)
    assert t.disbursement_id is None
    assert billing_svc.pending_disbursements(
        session, firm["p"], firm["case"].id) == []


# ================================================================ 用印


def test_seal_ticket_needs_document_and_copies(session, firm):
    with pytest.raises(DomainError) as e:
        _ticket(session, firm, kind="seal", subject="用印申请",
                seal_kind="firm", seal_copies=0, seal_document="委托书")
    assert e.value.code == "approval.seal_copies_required"

    with pytest.raises(DomainError) as e:
        _ticket(session, firm, kind="seal", subject="用印申请",
                seal_kind="firm", seal_copies=3, seal_document="")
    assert e.value.code == "approval.seal_document_required"


def test_seal_records_how_many_were_actually_used(session, firm):
    """★ 用印执行不是走个过场 —— 实际份数必须回填。"""
    t = _ticket(session, firm, kind="seal", subject="授权委托书用印",
                amount=Decimal("0"), seal_kind="firm",
                seal_document="民事授权委托书", seal_copies=3)
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    service.execute_seal(session, firm["ps"], t.id, used_copies=2)
    assert t.seal_used == 2
    assert t.status == "done"


def test_using_more_seals_than_approved_is_blocked(session, firm):
    """★ 申请 3 份盖了 5 份 —— 那两份盖在什么上、给了谁，查不清就是事故。"""
    t = _ticket(session, firm, kind="seal", subject="合同用印",
                amount=Decimal("0"), seal_kind="contract",
                seal_document="施工合同", seal_copies=3)
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    with pytest.raises(DomainError) as e:
        service.execute_seal(session, firm["ps"], t.id, used_copies=5)
    assert e.value.code == "approval.seal_over_used"
    assert "事故" in str(e.value)


def test_only_seal_role_can_execute(session, firm):
    """★ 盖章的人不该同时是批准盖章的人。"""
    t = _ticket(session, firm, kind="seal", subject="合同用印",
                amount=Decimal("0"), seal_kind="contract",
                seal_document="施工合同", seal_copies=1)
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    with pytest.raises(DomainError) as e:
        service.execute_seal(session, firm["pl"], t.id, used_copies=1)
    assert e.value.code == "approval.forbidden"


# ================================================================ 作废与锁


def test_paid_ticket_cannot_be_cancelled(session, firm):
    """★ 已经付出去的钱要走冲账，不是把单子一作废了事。"""
    t = _ticket(session, firm)
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    service.pay(session, firm["pq"], t.id)
    with pytest.raises(DomainError) as e:
        service.cancel(session, firm["pz"], t.id, "不要了")
    assert e.value.code == "approval.locked"
    assert "冲账" in str(e.value)


def test_cancel_needs_a_reason_and_keeps_the_record(session, firm):
    t = _ticket(session, firm)
    with pytest.raises(DomainError) as e:
        service.cancel(session, firm["pz"], t.id, " ")
    assert e.value.code == "approval.cancel_reason_required"

    service.cancel(session, firm["pz"], t.id, "法院改了缴费方式")
    assert t.status == "cancelled"
    assert len(service.my_tickets(session, firm["pz"])) == 1


def test_cannot_cancel_someone_elses_ticket(session, firm):
    t = _ticket(session, firm, principal=firm["pl"])
    with pytest.raises(DomainError) as e:
        service.cancel(session, firm["pz"], t.id, "手滑")
    assert e.value.code == "approval.not_yours"


def test_amount_required_for_money_tickets(session, firm):
    with pytest.raises(DomainError) as e:
        _ticket(session, firm, amount=Decimal("0"))
    assert e.value.code == "approval.amount_required"


def test_future_payment_is_refused(session, firm):
    t = _ticket(session, firm)
    service.submit(session, firm["pz"], t.id)
    service.decide(session, firm["pl"], t.id, approve=True)
    with pytest.raises(DomainError) as e:
        service.pay(session, firm["pq"], t.id,
                    paid_on=date.today() + timedelta(days=1))
    assert e.value.code == "approval.future_payment"


def test_seed_flows_is_idempotent_and_returns_the_whole_set(session, firm):
    first = service.ensure_default_flows(session, firm["tenant"].id)
    second = service.ensure_default_flows(session, firm["tenant"].id)
    assert len(second) == len(first) > 0
