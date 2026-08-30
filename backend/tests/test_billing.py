# -*- coding: utf-8 -*-
"""Step 17 · 账单。

**头号原则：算错一分钱财务要找半天；算错一个百分点可能是违规收费。**

所以重点测四件事：

  1. **法定红线拦得住** —— 风险代理禁区、30% 上限。这不是所里的规矩
  2. **钱算得对** —— 含税/不含税、代垫不计税、合计严格等于分项之和
  3. **发出去的钉死** —— 已开的账单改不了，红冲不是删除
  4. **收回来才算数** —— 回款独立一步，多收不许悄悄入账
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from kernel.errors import DomainError, NotFound
from modules.billing import service
from modules.billing.rules import CONTINGENCY_MAX_RATE
from modules.case import service as case_svc
from modules.identity import access
from modules.identity import service as identity
from modules.consult import service as consult_svc


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

    def person(name, role, position="senior_partner"):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, position)
        identity.assign_membership(session, u.id, t1.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    wang = person("wang", "committee")      # 管委会：全套
    li = person("li", "team_leader")        # 团队负责人：能签约定、编草稿
    zhang = person("zhang", "lawyer")       # 律师：只看
    qian = person("qian", "finance")        # 财务：开单、收款、费率

    def make_case(code, title, cause):
        return case_svc.create_case(
            session, t.id, code + sfx[:5], title, lead_id=wang.id,
            cause_id=causes[cause].id, org_unit_id=t1.id)

    return {
        "tenant": t, "causes": causes,
        "wang": wang, "li": li, "zhang": zhang, "qian": qian,
        "p": access.build_principal(session, wang.id),
        "pl": access.build_principal(session, li.id),
        "pz": access.build_principal(session, zhang.id),
        "pq": access.build_principal(session, qian.id),
        "case": make_case("JZ", "宏远诉凯达建设工程", "construction"),
        "labor_case": make_case("LD", "赵某某劳动争议", "labor"),
        "criminal_case": make_case("XS", "某某涉嫌合同诈骗", "criminal"),
        "admin_case": make_case("XZ", "某公司诉市监局", "admin_penalty"),
        "make_case": make_case,
    }


# ================================================================ ★ 法定红线


def test_criminal_case_cannot_be_contingency(session, firm):
    """★ 刑事诉讼案件**禁止**风险代理 —— 整类禁止，硬拦。

    这不是所里的规矩，是《律师服务收费管理办法》第十二条。
    违规收费的后果是司法行政机关处罚、退费，以及执业记录上留一笔。
    """
    chk = service.check_contingency(session, firm["p"],
                                    firm["criminal_case"].id, Decimal("0.2"))
    assert chk["level"] == "block"
    assert "第十二条" in chk["basis"]

    with pytest.raises(DomainError) as e:
        service.create_engagement(session, firm["p"], firm["criminal_case"].id,
                                  "contingency", contingency_rate=Decimal("0.2"))
    assert e.value.code == "billing.contingency_forbidden"
    # ★ 报错必须带依据 —— 一句"不允许"律师没法拿去跟客户解释
    assert "第十二条" in str(e.value)


def test_administrative_case_cannot_be_contingency(session, firm):
    """行政诉讼案件同样整类禁止。"""
    with pytest.raises(DomainError) as e:
        service.create_engagement(session, firm["p"], firm["admin_case"].id,
                                  "contingency", contingency_rate=Decimal("0.15"))
    assert e.value.code == "billing.contingency_forbidden"


def test_labor_case_needs_explicit_confirmation(session, firm):
    """★ 劳动争议是**部分禁止**，不能硬拦也不能放行。

    禁的是"请求支付劳动报酬"这个**诉讼请求**，不是案由本身 ——
    竞业限制补偿、违法解除赔偿金并不当然禁止。
    系统看不到具体诉请，**装作知道比不知道更危险**，
    所以要律师自己判断并签字确认。
    """
    chk = service.check_contingency(session, firm["p"],
                                    firm["labor_case"].id, Decimal("0.2"))
    assert chk["level"] == "confirm"
    assert "劳动报酬" in chk["reason"]
    # 说清自己盖不住什么
    assert "诉请" in chk["caveat"]

    with pytest.raises(DomainError) as e:
        service.create_engagement(session, firm["p"], firm["labor_case"].id,
                                  "contingency", contingency_rate=Decimal("0.2"))
    assert e.value.code == "billing.contingency_needs_confirm"

    eng = service.create_engagement(
        session, firm["p"], firm["labor_case"].id, "contingency",
        contingency_rate=Decimal("0.2"), confirm_legality=True)
    # ★ 确认要留痕 —— 事后能证明"系统提示过、律师确认过"
    assert eng.confirmed_by == firm["wang"].id
    assert "劳动报酬" in eng.legality_note
    assert "第十一条" in eng.legality_note


def test_contingency_rate_cap_is_thirty_percent(session, firm):
    """★ 30% 是法定上限（第十三条），**不看案由**，任何风险代理都管。"""
    assert CONTINGENCY_MAX_RATE == Decimal("0.30")

    with pytest.raises(DomainError) as e:
        service.create_engagement(session, firm["p"], firm["case"].id,
                                  "contingency", contingency_rate=Decimal("0.35"),
                                  confirm_legality=True)
    assert e.value.code == "billing.contingency_forbidden"
    assert "第十三条" in str(e.value)

    # 正好 30% 可以
    eng = service.create_engagement(session, firm["p"], firm["case"].id,
                                    "contingency", contingency_rate=Decimal("0.30"),
                                    confirm_legality=True)
    assert eng.contingency_rate == Decimal("0.30")


def test_cap_is_rechecked_when_billing(session, firm):
    """★ 开账单时**再查一遍**上限。

    约定可能被人改过，标的额也可能变（调解结案金额降了）。
    一道法定红线，宁可查两遍。
    """
    eng = service.create_engagement(session, firm["p"], firm["case"].id,
                                    "contingency", contingency_rate=Decimal("0.25"),
                                    confirm_legality=True)
    # 模拟有人绕过 service 直接改了库
    eng.contingency_rate = Decimal("0.40")
    session.flush()

    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    with pytest.raises(DomainError) as e:
        service.bill_contingency(session, firm["p"], inv.id,
                                 recovered=Decimal("1000000"))
    assert e.value.code == "billing.contingency_forbidden"


def test_unknown_cause_says_it_cannot_be_sure(session, firm):
    """★ 没匹配到禁区**不等于一定可以** —— 要明说这一点。

    系统只看得到案由，禁的是具体诉请。
    """
    chk = service.check_contingency(session, firm["p"], firm["case"].id,
                                    Decimal("0.2"))
    assert chk["level"] == "confirm"          # 建设工程属民事大类
    assert "逐条" in chk["caveat"] or "核对" in chk["caveat"]


# ================================================================ 钱算得对


@pytest.mark.parametrize("amount,rate,included,net,tax,gross", [
    # 含税报价 10000、6% —— 客户付 10000，其中税 566.04
    ("10000", "0.06", True, "9433.96", "566.04", "10000.00"),
    # 不含税报价 10000、6% —— 客户付 10600
    ("10000", "0.06", False, "10000.00", "600.00", "10600.00"),
    ("0", "0.06", True, "0.00", "0.00", "0.00"),
    # 小规模 3%
    ("5000", "0.03", True, "4854.37", "145.63", "5000.00"),
])
def test_tax_split(amount, rate, included, net, tax, gross):
    """★ 「这个价含不含税」是律所收费纠纷的前三名。系统不猜，按约定算。"""
    n, t, g = service.split_tax(Decimal(amount), Decimal(rate), included)
    assert (str(n), str(t), str(g)) == (net, tax, gross)


def test_total_always_equals_the_parts(session, firm):
    """★ 合计必须**严格等于**分项之和。

    客户拿计算器一加发现总数对不上分项，整张账单的可信度就没了。
    """
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    for amt in ("3333.33", "1666.67", "999.99"):
        service.add_line(session, firm["p"], inv.id, "起草与检索工作", amt)
    session.refresh(inv)
    assert inv.net_amount + inv.tax_amount == inv.total_amount
    assert inv.fee_amount == Decimal("5999.99")


def test_disbursements_are_not_taxed(session, firm):
    """★ 代垫费用是**替客户垫的钱**，原价转付，不能跟着计增值税。

    把它一起计税，等于凭空多收客户一笔。
    """
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草起诉状与证据整理",
                     Decimal("10000"), kind="fee")
    service.add_line(session, firm["p"], inv.id, "诉讼费（法院收据附后）",
                     Decimal("5000"), kind="disbursement")
    session.refresh(inv)

    assert inv.fee_amount == Decimal("10000.00")
    assert inv.disbursement_amount == Decimal("5000.00")
    # 税只按律师费 10000 算：600
    assert inv.tax_amount == Decimal("600.00")
    assert inv.total_amount == Decimal("15600.00")


def test_discount_reduces_the_taxable_base(session, firm):
    """折让要在计税**之前**减掉 —— 先计税再打折等于替客户多缴了税。"""
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    service.add_line(session, firm["p"], inv.id, "老客户折让", Decimal("2000"),
                     kind="discount")
    session.refresh(inv)
    assert inv.tax_amount == Decimal("480.00")      # 8000 × 6%
    assert inv.total_amount == Decimal("8480.00")


def test_money_rounds_half_up_not_bankers(session):
    """★ 四舍五入，不用银行家舍入。

    客户拿计算器按出来的是四舍五入，对不上的时候没法解释
    "0.125 舍成 0.12 是因为 2 是偶数"。
    """
    assert service.money("0.125") == Decimal("0.13")
    assert service.money("0.135") == Decimal("0.14")


def test_line_needs_a_description(session, firm):
    """账单每行都要写明是什么 —— 客户是逐行看、逐行砍的。"""
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    with pytest.raises(DomainError) as e:
        service.add_line(session, firm["p"], inv.id, "  ", Decimal("100"))
    assert e.value.code == "billing.line_description_required"


# ================================================================ 费率


def test_rate_is_looked_up_by_date(session, firm):
    """★ 费率有生效期。去年的工时今年开账单，用的是**去年的价**。

    不带日期查费率，等于把调价倒灌进历史账单。
    """
    service.set_rate(session, firm["p"], Decimal("2000"),
                     position_code="senior_partner",
                     valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31))
    service.set_rate(session, firm["p"], Decimal("3000"),
                     position_code="senior_partner", valid_from=date(2026, 1, 1))

    tid = firm["tenant"].id
    assert service.rate_for(session, tid, firm["wang"].id, "senior_partner",
                            on=date(2025, 6, 1)) == Decimal("2000.00")
    assert service.rate_for(session, tid, firm["wang"].id, "senior_partner",
                            on=date(2026, 6, 1)) == Decimal("3000.00")


def test_personal_rate_beats_position_rate(session, firm):
    """个人特价盖过职级价 —— 有的人就是有特价，那是所里谈好的。"""
    service.set_rate(session, firm["p"], Decimal("3000"),
                     position_code="senior_partner", valid_from=date(2026, 1, 1))
    service.set_rate(session, firm["p"], Decimal("5000"),
                     user_id=firm["wang"].id, valid_from=date(2026, 1, 1))
    assert service.rate_for(session, firm["tenant"].id, firm["wang"].id,
                            "senior_partner", on=date(2026, 6, 1)) == Decimal("5000.00")


def test_rate_needs_a_scope(session, firm):
    with pytest.raises(DomainError) as e:
        service.set_rate(session, firm["p"], Decimal("1000"))
    assert e.value.code == "billing.rate_scope_required"


# ================================================================ 从咨询开单
#
# ★ 原来这一段测的是"从工时开单"。工时模块已拆 ——
#   提成制律所不按工时给客户开账单，团队成员和助理是工资制/分成制。
#   真正按小时收钱的只有法律咨询，所以只剩这个。


def _client_of(session, firm):
    """账单要挂客户才能拉咨询 —— 给案件配一个。"""
    from modules.crm import service as crm_svc

    party = crm_svc.create_party(session, firm["tenant"].id,
                                 "咨询客户", "org", allow_similar=True)
    code = crm_svc.next_client_code(session, firm["tenant"].id)
    c = crm_svc.create_client(session, firm["tenant"].id, party.id, code,
                              owner_id=firm["wang"].id)
    firm["case"].client_id = c.id
    session.flush()
    return c


def _consult(session, firm, client, minutes=120,
             summary="解答工程款优先受偿权的行使期限", rate="3000",
             free=False, free_reason="", on=None):
    p = access.build_principal(session, firm["wang"].id)
    return consult_svc.log(
        session, p, client.id, on or date.today(), minutes,
        summary=summary, case_id=firm["case"].id,
        hourly_rate=Decimal(rate), free=free, free_reason=free_reason)


def test_pull_consult_makes_one_line_per_record(session, firm):
    """从咨询生成账单行，摘要直接用咨询记录里那句话。"""
    c = _client_of(session, firm)
    _consult(session, firm, c, 120, "解答工程款优先受偿权的行使期限")
    _consult(session, firm, c, 60, "解答竣工结算答复期限的算法")

    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    out = service.pull_consult(session, firm["p"], inv.id)
    assert out["lines"] == 2
    lines = service.lines_of(session, firm["p"], inv.id)
    # ★ 不按下标断言 —— 咨询是按日期倒序捞的，同一天的顺序没保证。
    #   写死 lines[0] 的测试会在别人改排序时莫名其妙地红，
    #   而那时候真正该问的是"排序改了对不对"，不是"这条测试怎么了"。
    text = "".join(l.description for l in lines)
    assert "优先受偿权" in text and "竣工结算" in text
    # 2 小时 × 3000 = 6000，1 小时 = 3000
    amounts = sorted(l.amount for l in lines)
    assert amounts == [Decimal("3000.00"), Decimal("6000.00")]


def test_amount_is_frozen_at_log_time_not_recomputed(session, firm):
    """★ 金额在**记咨询那一刻**就算好钉死了。

    费率以后调价，去年那条咨询不该跟着变 —— 这是记账的基本要求。
    """
    c = _client_of(session, firm)
    row = _consult(session, firm, c, 60, rate="1000")
    assert row.amount == Decimal("1000.00")

    # 所里涨价了
    service.set_rate(session, firm["p"], Decimal("5000"),
                     position_code="senior_partner", valid_from=date(2020, 1, 1))
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.pull_consult(session, firm["p"], inv.id)
    lines = service.lines_of(session, firm["p"], inv.id)
    # 还是当时那个价
    assert lines[0].amount == Decimal("1000.00")


def test_free_consult_never_reaches_an_invoice(session, firm):
    """免费咨询不进账单 —— 这条规则归 consult 判，billing 不该知道。"""
    c = _client_of(session, firm)
    _consult(session, firm, c, 30, free=True, free_reason="首次面谈")
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    out = service.pull_consult(session, firm["p"], inv.id)
    assert out["pulled"] == 0


def test_issue_marks_the_consult_as_billed(session, firm):
    """开出账单时把咨询推成已出账 —— 通过 consult 的 api，不碰它的表。"""
    c = _client_of(session, firm)
    row = _consult(session, firm, c, 120)
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.pull_consult(session, firm["p"], inv.id)
    service.issue(session, firm["p"], inv.id)

    session.refresh(row)
    assert row.status == "billed"
    # 再拉一次拉不到了 —— 不会重复开单
    inv2 = service.create_invoice(session, firm["p"], firm["case"].id)
    assert service.pull_consult(session, firm["p"], inv2.id)["pulled"] == 0


def test_consult_is_not_locked_while_the_invoice_is_a_draft(session, firm):
    """★ 草稿阶段**不锁**。

    草稿还会加加减减，那时候就锁，等于用户删掉一行之后那条咨询再也回不来了。
    """
    c = _client_of(session, firm)
    row = _consult(session, firm, c, 120)
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.pull_consult(session, firm["p"], inv.id)
    session.refresh(row)
    assert row.status == "draft"          # 还没锁


# ================================================================ 固定与风险


def test_fixed_fee_with_tax_included_is_converted_to_net(session, firm):
    """★ 约定含税时，账单行存的是**不含税**金额。

    不换算的话含税报价会被再计一次税，客户凭空多付 6%。
    """
    service.create_engagement(session, firm["p"], firm["case"].id, "fixed",
                              fixed_amount=Decimal("106000"), tax_included=True)
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    line = service.bill_fixed(session, firm["p"], inv.id)
    assert line.amount == Decimal("100000.00")
    session.refresh(inv)
    # 客户实付回到 106000，不是 112360
    assert inv.total_amount == Decimal("106000.00")


def test_contingency_line_is_percent_of_recovery(session, firm):
    service.create_engagement(session, firm["p"], firm["case"].id, "contingency",
                              contingency_rate=Decimal("0.2"),
                              tax_included=False, confirm_legality=True)
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    line = service.bill_contingency(session, firm["p"], inv.id,
                                    recovered=Decimal("1000000"))
    assert line.amount == Decimal("200000.00")
    assert "20" in line.description


def test_contingency_needs_a_base(session, firm):
    """风险代理要按实际回款算，基数是 0 就开不了 —— 不能默默算成 0。"""
    service.create_engagement(session, firm["p"], firm["case"].id, "contingency",
                              contingency_rate=Decimal("0.2"),
                              confirm_legality=True)
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    with pytest.raises(DomainError) as e:
        service.bill_contingency(session, firm["p"], inv.id)
    assert e.value.code == "billing.contingency_base_required"


def test_mode_mismatch_is_refused(session, firm):
    """按计时约定的案子不能走固定收费那条路。"""
    service.create_engagement(session, firm["p"], firm["case"].id, "hourly")
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    with pytest.raises(DomainError) as e:
        service.bill_fixed(session, firm["p"], inv.id, Decimal("10000"))
    assert e.value.code == "billing.mode_mismatch"


def test_new_engagement_deactivates_the_old_one(session, firm):
    """★ 一个案子只留一份生效的约定 —— 否则开账单时不知道按哪份算。"""
    a = service.create_engagement(session, firm["p"], firm["case"].id, "hourly")
    b = service.create_engagement(session, firm["p"], firm["case"].id, "fixed",
                                  fixed_amount=Decimal("50000"))
    session.refresh(a)
    assert a.active is False
    assert service.engagement_of(session, firm["p"], firm["case"].id).id == b.id


# ================================================================ 开出与锁死


def test_issued_invoice_cannot_be_edited(session, firm):
    """★ 改一张已经发出去的账单，等于两份文件在世上打架。"""
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    service.issue(session, firm["p"], inv.id)

    with pytest.raises(DomainError) as e:
        service.add_line(session, firm["p"], inv.id, "又想起一笔", Decimal("500"))
    assert e.value.code == "billing.locked"
    assert "红冲" in str(e.value)


def test_empty_invoice_cannot_be_issued(session, firm):
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    with pytest.raises(DomainError) as e:
        service.issue(session, firm["p"], inv.id)
    assert e.value.code == "billing.empty_invoice"


def test_void_keeps_the_record(session, firm):
    """★ 红冲不是删除 —— 一张凭空消失的账单比一张错的更可疑。"""
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    service.issue(session, firm["p"], inv.id)
    service.void_invoice(session, firm["p"], inv.id, "开错了客户抬头")
    assert inv.status == "void"
    assert "抬头" in inv.void_reason
    # 库里还在
    assert len(service.invoices_of_case(session, firm["p"], firm["case"].id)) == 1


def test_void_needs_a_reason(session, firm):
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    service.issue(session, firm["p"], inv.id)
    with pytest.raises(DomainError) as e:
        service.void_invoice(session, firm["p"], inv.id, "  ")
    assert e.value.code == "billing.void_reason_required"


def test_paid_invoice_cannot_be_voided_directly(session, firm):
    """★ 收过钱的账单不能直接红冲 —— 那笔钱去哪了得有交代。"""
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    service.issue(session, firm["p"], inv.id)
    service.record_payment(session, firm["p"], inv.id, Decimal("1000"))
    with pytest.raises(DomainError) as e:
        service.void_invoice(session, firm["p"], inv.id, "开错了")
    assert e.value.code == "billing.has_payment"
    assert "冲回" in str(e.value)


# ================================================================ 回款


def _issued(session, firm, amount="10600"):
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭",
                     Decimal(amount) / Decimal("1.06"))
    return service.issue(session, firm["p"], inv.id)


def test_payment_moves_status_through_partial_to_paid(session, firm):
    """★ 开了账单不等于收到钱。"""
    inv = _issued(session, firm)
    assert inv.status == "issued"
    total = inv.total_amount

    service.record_payment(session, firm["p"], inv.id, total / 2)
    assert inv.status == "partial"
    service.record_payment(session, firm["p"], inv.id,
                           service.money(total - inv.paid_amount))
    assert inv.status == "paid"
    assert inv.paid_amount == total


def test_overpayment_is_refused(session, firm):
    """★ 多收的钱不能悄悄入账。

    客户付错了？合并付款？两种都得人来判断 —— 系统替他决定就是替他扛责任。
    """
    inv = _issued(session, firm)
    with pytest.raises(DomainError) as e:
        service.record_payment(session, firm["p"], inv.id,
                               inv.total_amount + Decimal("1"))
    assert e.value.code == "billing.overpay"


def test_cannot_pay_a_draft(session, firm):
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    with pytest.raises(DomainError) as e:
        service.record_payment(session, firm["p"], inv.id, Decimal("100"))
    assert e.value.code == "billing.not_issued"


def test_future_payment_is_refused(session, firm):
    inv = _issued(session, firm)
    with pytest.raises(DomainError) as e:
        service.record_payment(session, firm["p"], inv.id, Decimal("100"),
                               paid_on=date.today() + timedelta(days=1))
    assert e.value.code == "billing.future_payment"


def test_reversing_a_payment_restores_the_balance(session, firm):
    """★ 冲回不删除 —— 钱的记录不能凭空消失。"""
    inv = _issued(session, firm)
    p = service.record_payment(session, firm["p"], inv.id, Decimal("5000"),
                               reference="BANK-2026-0001")
    assert inv.status == "partial"
    service.reverse_payment(session, firm["p"], p.id, "客户付错了，已退回")
    assert inv.paid_amount == Decimal("0.00")
    assert inv.status == "issued"
    assert p.reversed is True
    assert len(service.payments_of(session, firm["p"], inv.id)) == 1


def test_reverse_needs_a_reason(session, firm):
    inv = _issued(session, firm)
    p = service.record_payment(session, firm["p"], inv.id, Decimal("100"))
    with pytest.raises(DomainError) as e:
        service.reverse_payment(session, firm["p"], p.id, " ")
    assert e.value.code == "billing.reverse_reason_required"


# ================================================================ 账龄与权限


def test_receivables_buckets_by_age(session, firm):
    """★ 主角是账龄不是总额 —— 挂了 200 天的应收和上周开的不是一回事。"""
    inv = _issued(session, firm)
    inv.issued_on = date.today() - timedelta(days=200)
    inv.due_on = date.today() - timedelta(days=170)
    session.flush()

    r = service.receivables(session, firm["p"])
    assert r["invoice_count"] == 1
    assert r["items"][0]["age_days"] >= 200
    assert r["items"][0]["overdue"] is True
    assert Decimal(r["overdue_amount"]) > 0
    old = [b for b in r["aging"] if b["label"] == "180 天以上"][0]
    assert old["count"] == 1


def test_lawyer_cannot_sign_or_issue(session, firm):
    """★ 三件事各有各的把关人：谈收费、开账单、确认收款。"""
    with pytest.raises(DomainError) as e:
        service.create_engagement(session, firm["pz"], firm["case"].id, "hourly")
    assert e.value.code == "billing.forbidden"

    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    with pytest.raises(DomainError) as e:
        service.issue(session, firm["pz"], inv.id)
    assert e.value.code == "billing.forbidden"


def test_team_leader_can_sign_but_not_issue(session, firm):
    """★ 业务口能谈收费、能编草稿，但**开单和收款是财务的事** ——
    业务口开单、业务口销账，就没有第二双眼睛了。"""
    service.create_engagement(session, firm["pl"], firm["case"].id, "hourly")
    inv = service.create_invoice(session, firm["pl"], firm["case"].id)
    service.add_line(session, firm["pl"], inv.id, "起草与出庭", Decimal("10000"))
    with pytest.raises(DomainError) as e:
        service.issue(session, firm["pl"], inv.id)
    assert e.value.code == "billing.forbidden"


def test_finance_can_issue_but_not_change_the_deal(session, firm):
    """★ 财务开单收款，**不给 billing.manage** ——
    收费怎么谈是业务的事，财务改了约定就说不清是谁答应客户的。"""
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.add_line(session, firm["p"], inv.id, "起草与出庭", Decimal("10000"))
    service.issue(session, firm["pq"], inv.id)          # 财务能开
    service.record_payment(session, firm["pq"], inv.id, Decimal("100"))  # 能收

    with pytest.raises(DomainError) as e:
        service.create_engagement(session, firm["pq"], firm["case"].id, "fixed",
                                  fixed_amount=Decimal("1"))
    assert e.value.code == "billing.forbidden"


def test_case_billing_shows_the_whole_picture(session, firm):
    service.create_engagement(session, firm["p"], firm["case"].id, "fixed",
                              fixed_amount=Decimal("50000"))
    inv = _issued(session, firm)
    service.record_payment(session, firm["p"], inv.id, Decimal("6000"))

    s = service.case_billing(session, firm["p"], firm["case"].id)
    assert s["invoice_count"] == 1
    assert Decimal(s["paid"]) == Decimal("6000.00")
    assert Decimal(s["outstanding"]) == Decimal(s["billed"]) - Decimal("6000.00")
    assert s["engagement"]["mode"] == "fixed"


def test_invoice_numbers_are_unique_and_sequential(session, firm):
    a = service.create_invoice(session, firm["p"], firm["case"].id)
    b = service.create_invoice(session, firm["p"], firm["case"].id)
    assert a.number != b.number
    assert a.number.startswith("ZD")


def test_check_message_has_no_duplicated_lead_in(session, firm):
    """★ message 只放实质内容，引子由调用方按场景加。

    界面上标题已经写了「这个案由部分禁止风险代理」，后端 message
    再说一遍，同一句话连着出现两次 —— 读的人会以为哪里出错了。
    """
    chk = service.check_contingency(session, firm["p"],
                                    firm["labor_case"].id, Decimal("0.2"))
    assert chk["level"] == "confirm"
    assert not chk["message"].startswith("这个案由")
    assert "劳动报酬" in chk["message"] and "第十一条" in chk["message"]

    # 但报错那条路没有标题，引子要补上
    with pytest.raises(DomainError) as e:
        service.create_engagement(session, firm["p"], firm["labor_case"].id,
                                  "contingency", contingency_rate=Decimal("0.2"))
    assert str(e.value).startswith("这个案由")


# ================================================================ 代垫费用


def test_recorded_disbursement_flows_into_an_invoice(session, firm):
    """★ 垫了钱就挂在案子上等着开票，不靠人记得。

    垫钱和开账单差着好几个月 —— 律师三月替客户垫了鉴定费，
    六月开账单时全靠翻财务凭证回忆。翻漏了就是律所自己贴钱。
    """
    d = service.record_disbursement(session, firm["p"], firm["case"].id,
                                    Decimal("3000"), "造价鉴定费垫付",
                                    spent_on=date(2026, 3, 10))
    assert service.pending_disbursements(session, firm["p"],
                                         firm["case"].id) == [d]

    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    out = service.pull_disbursements(session, firm["p"], inv.id)
    assert out["pulled"] == 1
    session.refresh(inv)
    # 代垫不计增值税
    assert inv.disbursement_amount == Decimal("3000.00")
    assert inv.tax_amount == Decimal("0.00")


def test_disbursement_is_locked_only_at_issue(session, firm):
    """★ 和工时一样：草稿阶段不锁，否则删掉一行代垫就再也回不来了。"""
    service.record_disbursement(session, firm["p"], firm["case"].id,
                                Decimal("3000"), "造价鉴定费垫付")
    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.pull_disbursements(session, firm["p"], inv.id)
    # 还没开出，代垫还是"待开票"
    assert len(service.pending_disbursements(session, firm["p"],
                                             firm["case"].id)) == 1
    service.issue(session, firm["p"], inv.id)
    assert service.pending_disbursements(session, firm["p"],
                                         firm["case"].id) == []


def test_mixed_invoice_with_time_and_disbursement_issues_cleanly(session, firm):
    """★ 这条测试钉住一个真出过的 bug。

    账单行的 source_entry_ids 混着两种来源：工时是纯数字，代垫是 "D123"。
    第一版开票时无差别 `int()` 一遍，一张同时有明细和代垫的账单
    **直接 ValueError 开不出去** —— 而这恰恰是最常见的一种账单。

    ★ 现在前缀有两种：C=咨询、D=代垫。读的时候**只挑自己认识的**，
      不认识的一律跳过 —— 绝不假设"剩下的都是我的"。
    """
    c = _client_of(session, firm)
    e = _consult(session, firm, c, 120)
    service.record_disbursement(session, firm["p"], firm["case"].id,
                                Decimal("500"), "诉讼费垫付")

    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.pull_consult(session, firm["p"], inv.id)
    service.pull_disbursements(session, firm["p"], inv.id)
    service.issue(session, firm["p"], inv.id)      # ★ 不该炸

    session.refresh(e)
    assert e.status == "billed"                    # 咨询锁了
    assert service.pending_disbursements(session, firm["p"],
                                         firm["case"].id) == []   # 代垫也锁了
    session.refresh(inv)
    assert inv.fee_amount == Decimal("6000.00")
    assert inv.disbursement_amount == Decimal("500.00")
    # 税只按律师费算
    assert inv.tax_amount == Decimal("360.00")
    assert inv.total_amount == Decimal("6860.00")


def test_disbursement_needs_a_description(session, firm):
    """这句话会原样进账单，客户要看得懂。"""
    with pytest.raises(DomainError) as e:
        service.record_disbursement(session, firm["p"], firm["case"].id,
                                    Decimal("100"), "  ")
    assert e.value.code == "billing.line_description_required"


def test_finance_can_issue_an_invoice_containing_consult(session, firm):
    """★ 主路径：**财务**开一张含咨询费的账单。

    这条测试钉住一个真出过的 bug（当时还是工时）：`mark_billed` 原来要
    `time.approve`，而财务被刻意不给那个权限 ——
    **于是财务开不出任何含工时的账单**，而那正是最常走的一条路。

    错在那道检查越权了：「谁可以出账」是 billing 的判断，不是记录方的。
    一个模块替另一个模块判断授权，两边的权限设计迟早对不上。

    ★ 工时模块拆了，但这个坑照样能再踩一遍 —— 所以这条测试留着，
      只是把主角换成咨询。财务没有 consult.log，照样得开得出账单。
    """
    c = _client_of(session, firm)
    e = _consult(session, firm, c, 120)

    inv = service.create_invoice(session, firm["p"], firm["case"].id)
    service.pull_consult(session, firm["p"], inv.id)
    # ★ 用财务账号开
    service.issue(session, firm["pq"], inv.id)

    session.refresh(e)
    assert e.status == "billed"
    assert inv.status == "issued"
