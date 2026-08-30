# -*- coding: utf-8 -*-
"""给演示库造几个月的创收数据。

★ **刻意造"不好看"的数据。** 全是漂亮数字的看板验不出任何东西 ——
  你看不出"哪个数该标红"、"排序对不对"、"空状态什么样"。

  所以这里一定有：
    · 一笔挂了 200 天的应收（账龄最老那档）
    · 一个开了单一分没收的案子
    · 一个只有草稿账单、从没开出去的案子
    · 有人创收很多，有人几乎没有

用法：
    python -m scripts.seed_demo_revenue          # 造
    python -m scripts.seed_demo_revenue --clean  # 清掉（只清本脚本造的）
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from kernel.bootstrap import bootstrap
from kernel.db.session import session_scope

#: 本脚本造的东西都带这个前缀，清理时按它认
TAG = "DEMO-REV"


def _p(session, access, user_id):
    return access.build_principal(session, user_id)


def clean():
    bootstrap()
    from modules.billing.models import Disbursement, Invoice, InvoiceLine, Payment
    from modules.case.models import Case, CaseParty, CaseStatusLog
    from modules.crm.models import Party

    with session_scope() as s:
        ids = [c.id for c in s.execute(
            select(Case).where(Case.code.like(TAG + "%"))).scalars()]
        if ids:
            invs = [i.id for i in s.execute(
                select(Invoice).where(Invoice.case_id.in_(ids))).scalars()]
            if invs:
                s.execute(delete(Payment).where(Payment.invoice_id.in_(invs)))
                s.execute(delete(InvoiceLine).where(
                    InvoiceLine.invoice_id.in_(invs)))
                s.execute(delete(Invoice).where(Invoice.id.in_(invs)))
            s.execute(delete(Disbursement).where(Disbursement.case_id.in_(ids)))
            for M in (CaseStatusLog, CaseParty):
                s.execute(delete(M).where(M.case_id.in_(ids)))
            s.execute(delete(Case).where(Case.id.in_(ids)))
        n = s.execute(delete(Party).where(Party.name.like(TAG + "%"))).rowcount
        print("清掉 %d 个演示案件、%d 个主体" % (len(ids), n))


def seed():
    bootstrap()
    from modules.billing import service as bill
    from modules.case import service as case_svc
    from modules.case import models as cm
    from modules.conflict import service as conflict
    from modules.crm import service as crm
    from modules.identity import access, models as im

    today = date.today()

    with session_scope() as s:
        users = {u.full_name: u for u in s.execute(select(im.User)).scalars()}
        boss = next(u for u in users.values()
                    if "billing.issue" in set(_p(s, access, u.id).permissions))
        tenant = boss.tenant_id
        P = _p(s, access, boss.id)

        # 挑几个能当主办的人 —— 创收要归到人
        leads = [u for u in users.values()
                 if "case.manage" in set(_p(s, access, u.id).permissions)][:4]
        if len(leads) < 2:
            leads = list(users.values())[:3]
        print("主办律师：", "、".join(u.full_name for u in leads))

        causes = {c.code: c for c in s.execute(
            select(cm.CauseOfAction)).scalars()}
        orgs = list(s.execute(select(im.OrgUnit).where(
            im.OrgUnit.kind == "team")).scalars())

        # 费率（计时收费的案子要用）
        try:
            bill.set_rate(s, P, Decimal("3000"),
                          position_code="senior_partner",
                          valid_from=date(2020, 1, 1))
        except Exception:                                # noqa: BLE001
            pass

        #: (后缀, 案由, 主办序号, 收费模式, 金额, 开票距今天数, 收了几成)
        #:
        #: ★ 最后三个是**刻意难看**的：
        #:     一笔挂 200 天没收、一笔开了一分没收、一个只有草稿
        PLAN = [
            ("A", "construction", 0, "fixed",       Decimal("120000"),  12, 1.0),
            ("B", "construction", 0, "fixed",       Decimal("86000"),   40, 1.0),
            ("C", "labor",        1, "fixed",       Decimal("45000"),   25, 0.5),
            ("D", "sales",        1, "hourly",      None,               18, 1.0),
            ("E", "loan",         2 % max(len(leads), 1), "fixed",
                                                    Decimal("68000"),   60, 0.3),
            # ---- 难看的三个 ----
            ("X", "construction", 0, "fixed",       Decimal("230000"), 200, 0.0),
            ("Y", "equity",       1, "fixed",       Decimal("95000"),   75, 0.0),
            ("Z", "sales",        0, "fixed",       Decimal("52000"),  None, 0.0),
        ]

        made = 0
        for sfx, cause, li, mode, amount, issued_ago, paid_ratio in PLAN:
            lead = leads[li % len(leads)]
            org = orgs[li % len(orgs)] if orgs else None
            opened = today - timedelta(days=(issued_ago or 30) + 40)

            c = case_svc.create_case(
                s, tenant, "{0}-{1}".format(TAG, sfx),
                "演示案件 {0}".format(sfx), lead_id=lead.id,
                cause_id=causes[cause].id if cause in causes else None,
                org_unit_id=org.id if org else None,
                opened_on=opened)
            s.flush()

            party = crm.create_party(s, tenant, "{0}对方{1}".format(TAG, sfx),
                                     "org", allow_similar=True)
            case_svc.add_party(s, tenant, c.id, party.id, "defendant")

            # 走到办理中
            case_svc.transition(s, c.id, "conflict_check", actor_id=boss.id)
            chk = conflict.run_check(s, tenant, c.id, requested_by=boss.id)
            if chk.status not in conflict.PASSING_STATUSES:
                conflict.decide(s, chk.id, "waived", boss.id, "演示数据放行")
            for to in ("pending", "active"):
                case_svc.transition(s, c.id, to, actor_id=boss.id)

            # 收费约定
            if mode == "fixed":
                bill.create_engagement(s, P, c.id, "fixed",
                                       fixed_amount=amount, tax_included=True)
            else:
                bill.create_engagement(s, P, c.id, "hourly", tax_included=False)

            # 账单
            inv = bill.create_invoice(
                s, P, c.id,
                due_on=(today - timedelta(days=issued_ago - 30))
                       if issued_ago else None)
            if mode == "fixed":
                bill.bill_fixed(s, P, inv.id)
            else:
                bill.add_line(s, P, inv.id, "法律服务费（按计时）",
                              Decimal("36000"), kind="fee")

            if issued_ago is None:
                # ★ 只有草稿、从没开出去 —— 看板要能把它挑出来
                made += 1
                continue

            bill.issue(s, P, inv.id,
                       issued_on=today - timedelta(days=issued_ago))
            if paid_ratio > 0:
                amt = bill.money(inv.total_amount * Decimal(str(paid_ratio)))
                bill.record_payment(
                    s, P, inv.id, amt,
                    paid_on=today - timedelta(days=max(issued_ago - 20, 0)),
                    reference="{0}-{1}".format(TAG, sfx))
            made += 1

        print("造了 %d 个演示案件（含 3 个刻意难看的）" % made)


if __name__ == "__main__":
    if "--clean" in sys.argv:
        clean()
    else:
        seed()
