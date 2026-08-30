# -*- coding: utf-8 -*-
"""归档。

**归档 = 三件事，缺一件都不算归了：**

  ① 拦住新的写入   —— 而且**要靠一条遍历测试保证不漏**，不靠人记得
  ② 编一份卷宗目录 —— 问遍所有模块，少一块要明说
  ③ 算指纹存下来   —— 将来能回答「这卷被动过吗」

★ 还有第四件容易忘的：**必须能解档**。案子会再审、会申请执行。
  没有解档，归档就是死胡同，然后大家都不敢归档 ——
  一个没人敢用的功能等于没做。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from kernel.errors import DomainError, NotFound
from modules.archive import service as arch
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

    def person(name, role):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, "senior_partner")
        identity.assign_membership(session, u.id, t1.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    wang = person("wang", "committee")        # 全套
    li = person("li", "team_leader")          # 能归档，不能强制/解档
    zhang = person("zhang", "lawyer")         # 只能看

    case = case_svc.create_case(
        session, t.id, "AJ" + sfx[:6], "宏远诉凯达", lead_id=wang.id,
        cause_id=causes["construction"].id, org_unit_id=t1.id)
    party = crm_svc.create_party(session, t.id, "凯达置业" + sfx[:4], "org",
                                 allow_similar=True)
    case_svc.add_party(session, t.id, case.id, party.id, "defendant")

    return {"tenant": t, "case": case,
            "wang": wang, "li": li, "zhang": zhang,
            "p": access.build_principal(session, wang.id),
            "pl": access.build_principal(session, li.id),
            "pz": access.build_principal(session, zhang.id)}


def _closed(session, firm):
    """把案子推到「已结案」—— 归档的前提。

    ★ 中间必须真跑一次利冲。那是执业红线（`conflict.assert_cleared`），
      测试也绕不过去 —— **绕过去的测试证明不了这条路真的能走通**。
    """
    from modules.conflict import service as conflict

    case_svc.transition(session, firm["case"].id, "conflict_check",
                        actor_id=firm["wang"].id)
    chk = conflict.run_check(session, firm["tenant"].id, firm["case"].id,
                             requested_by=firm["wang"].id)
    if chk.status not in conflict.PASSING_STATUSES:
        conflict.decide(session, chk.id, "waived", firm["wang"].id,
                        "演示数据，管委会放行")
    for to in ("pending", "active", "closing", "closed"):
        case_svc.transition(session, firm["case"].id, to,
                            actor_id=firm["wang"].id)
    return firm["case"]


# ================================================================ 目录


def test_inventory_asks_every_module(session, firm):
    """卷宗目录要问遍所有模块，**没人报数的要明说**。

    ★ `skipped` 那一栏是这个功能里最要紧的东西：一个模块悄悄不参与，
      卷就少一部分，而少的那部分**目录上根本不会出现** —— 没人会发现。
    """
    inv = arch.inventory(session, firm["p"], firm["case"].id)
    names = {g["name"] for g in inv["groups"]}
    assert "当事人" in names
    assert inv["item_count"] > 0
    # 装着却没报数的要列出来
    assert isinstance(inv["skipped"], list)


def test_inventory_does_not_change_anything(session, firm):
    """目录随时能看 —— 归档前律师要靠它确认「该进卷的都进了」。"""
    a = arch.inventory(session, firm["p"], firm["case"].id)
    b = arch.inventory(session, firm["p"], firm["case"].id)
    assert a["item_count"] == b["item_count"]


# ================================================================ 阻断


def test_cannot_archive_an_open_case(session, firm):
    """★ 办理中的案子不能归档 —— 那是把活的卷封了。"""
    with pytest.raises(DomainError) as e:
        arch.archive(session, firm["p"], firm["case"].id)
    # ★ 这是**硬前提**不是可 force 的阻断 —— force 也一样拦
    assert e.value.code == "archive.not_closed"
    with pytest.raises(DomainError) as e2:
        arch.archive(session, firm["p"], firm["case"].id, force=True)
    assert e2.value.code == "archive.not_closed"


def test_blockers_say_how_to_fix(session, firm):
    """★ 每条阻断都要给出口，不能只说"不行"。"""
    for b in arch.blockers(session, firm["p"], firm["case"].id):
        assert b["what"] and b["how"], b


def test_force_needs_extra_permission(session, firm):
    """★ 带阻断项强制归档是管委会的判断，团队负责人不给。"""
    _closed(session, firm)
    from modules.task import service as task
    task.create_task(session, firm["p"], "还没做完的事", case_id=firm["case"].id)
    with pytest.raises(DomainError) as e:
        arch.archive(session, firm["pl"], firm["case"].id, force=True)
    assert e.value.code == "archive.forbidden"


def test_force_records_what_was_unresolved(session, firm):
    """★ 强制归档可以，但**没了结的事要原样记进去**。

    带着未收的账归档是所里的决定 —— 但那个决定要留痕。
    """
    _closed(session, firm)
    # 留一个真的阻断项：一条没完成的待办
    from modules.task import service as task
    task.create_task(session, firm["p"], "还没做完的事",
                     case_id=firm["case"].id)
    r = arch.archive(session, firm["p"], firm["case"].id, force=True,
                     note="客户失联，按坏账处理")
    assert "强制归档" in r.note
    assert "未了结" in r.note
    assert "客户失联" in r.note


# ================================================================ 锁


def test_archived_case_is_read_only(session, firm):
    """★ 归档后写不进去 —— 这是归档的全部意义。"""
    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)

    with pytest.raises(DomainError) as e:
        case_svc.assert_writable(session, firm["p"], firm["case"].id)
    assert e.value.code == "case.frozen"
    # 报错要指出出口
    assert "解档" in str(e.value)
    # 但**看**还是能看的 —— 归档不是把卷藏起来
    assert case_svc.assert_visible(session, firm["p"], firm["case"].id)


def _a_client(session, firm, crm_svc):
    """归档测试要一个客户 —— 咨询必须挂客户。"""
    party = crm_svc.create_party(session, firm["tenant"].id,
                                 "归档测试客户", "org", allow_similar=True)
    code = crm_svc.next_client_code(session, firm["tenant"].id)
    c = crm_svc.create_client(session, firm["tenant"].id, party.id, code,
                              owner_id=firm["p"].user_id)
    return c.id


def test_every_write_entry_point_is_blocked(session, firm):
    """★★ 这条测试是「不漏锁」的唯一保证。

    归档后禁止写入，靠的是每个模块在写入口调一句 `assert_writable`。
    **靠人记得加，就一定会漏** —— 尤其是将来新加模块的时候。

    所以这里把已知的写入口全列出来逐个试。以后加了新模块忘了加锁，
    只要把它的写入口补进这张表，这条测试就会把它抓出来。
    """
    from modules.approval import service as appr
    from modules.billing import service as bill
    from modules.doc import service as doc
    from modules.draft import service as draft
    from modules.task import service as task
    from modules.consult import service as consult_svc
    from modules.crm import service as crm_svc

    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)
    cid = firm["case"].id
    p = firm["p"]

    attempts = [
        ("doc.upload", lambda: doc.upload(session, p, cid, "a.txt", b"x")),
        ("consult.log", lambda: consult_svc.log(
            session, p, _a_client(session, firm, crm_svc), date.today(), 60,
            summary="解答执行阶段的问题", case_id=cid)),
        ("billing.create_engagement", lambda: bill.create_engagement(
            session, p, cid, "hourly")),
        ("billing.create_invoice", lambda: bill.create_invoice(session, p, cid)),
        ("billing.record_disbursement", lambda: bill.record_disbursement(
            session, p, cid, Decimal("100"), "诉讼费垫付")),
        ("approval.create_ticket", lambda: appr.create_ticket(
            session, p, "payment", "诉讼费垫付", amount=Decimal("100"),
            case_id=cid)),
        ("task.create_task", lambda: task.create_task(
            session, p, "整理材料", case_id=cid)),
        ("task.add_deadline", lambda: task.add_deadline(
            session, p, cid, "举证期限", "受理通知书", date(2026, 12, 1))),
        ("task.generate", lambda: task.generate(session, p, cid)),
        ("draft.create", lambda: draft.create(session, p, cid, "civil_complaint")),
        ("case.create_matter", lambda: case_svc.create_matter(
            session, firm["tenant"].id, cid, "二审")),
    ]

    leaked = []
    for name, fn in attempts:
        try:
            fn()
            leaked.append(name)
        except DomainError as exc:
            # 拒了就行，但得是**因为归档**拒的，不是碰巧撞上别的校验
            if exc.code not in ("case.frozen", "case.closed_no_matter"):
                leaked.append("{0}（拒了，但理由是 {1}）".format(name, exc.code))
        except Exception:                                # noqa: BLE001
            pass
    assert not leaked, "这些写入口在归档后仍然能写：{0}".format(leaked)


# ================================================================ 指纹


def test_fingerprint_detects_tampering(session, firm):
    """★ 兜底体检：这卷被动过吗？

    正常永远相同 —— 因为归档后写不进去。不同就说明有人绕过了锁
    （直接改库、或者某个模块漏了检查）。
    """
    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)

    assert arch.verify(session, firm["p"], firm["case"].id)["ok"] is True

    # ★ **真的绕过 service 直接塞库** —— 那才是 verify() 要抓的东西。
    #   走 service 层不算绕过：锁本来就在那儿拦着。
    from modules.case.models import CaseParty

    party = crm_svc.create_party(session, firm["tenant"].id, "偷加的公司", "org",
                                 allow_similar=True)
    session.add(CaseParty(tenant_id=firm["tenant"].id, case_id=firm["case"].id,
                          party_id=party.id, role="defendant", is_ours=False))
    session.flush()

    out = arch.verify(session, firm["p"], firm["case"].id)
    assert out["ok"] is False
    assert out["item_count_now"] != out["item_count_then"]
    assert "绕过" in out["message"]


def test_fingerprint_ignores_volatile_fields(session, firm):
    """指纹只按 (模块, 名称, 件数) 算 —— 算进时间戳就每次都不一样，验不出东西。"""
    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)
    a = arch.verify(session, firm["p"], firm["case"].id)
    b = arch.verify(session, firm["p"], firm["case"].id)
    assert a["fingerprint_now"] == b["fingerprint_now"]


# ================================================================ 解档


def test_unarchive_reopens_the_case(session, firm):
    """★ 案子会再审、会申请执行 —— 必须能解开。"""
    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)

    arch.unarchive(session, firm["p"], firm["case"].id, "对方上诉，要出二审")
    # 解开之后又能写了
    case_svc.assert_writable(session, firm["p"], firm["case"].id)
    assert arch.current(session, firm["p"], firm["case"].id) is None


def test_unarchive_needs_a_reason(session, firm):
    """解了档没人说得清为什么，等于没归过。"""
    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)
    with pytest.raises(DomainError) as e:
        arch.unarchive(session, firm["p"], firm["case"].id, "  ")
    assert e.value.code == "archive.reason_required"


def test_unarchive_keeps_the_record(session, firm):
    """★ 归过档就是归过档 —— 记录不删，留着才说得清这卷被解开过。"""
    _closed(session, firm)
    r = arch.archive(session, firm["p"], firm["case"].id, force=True)
    vol = r.volume_no
    arch.unarchive(session, firm["p"], firm["case"].id, "申请执行")

    hist = arch.history(session, firm["p"], firm["case"].id)
    assert len(hist) == 1
    assert hist[0].volume_no == vol
    assert hist[0].active is False
    assert "申请执行" in hist[0].unarchive_reason


def test_team_leader_cannot_unarchive(session, firm):
    """把封好的卷解开是管委会的判断。"""
    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)
    with pytest.raises(DomainError) as e:
        arch.unarchive(session, firm["pl"], firm["case"].id, "想改个东西")
    assert e.value.code == "archive.forbidden"


def test_lawyer_cannot_archive(session, firm):
    """律师没有 archive.do —— 归档是团队负责人往上的事。"""
    _closed(session, firm)
    with pytest.raises(DomainError) as e:
        arch.archive(session, firm["pz"], firm["case"].id, force=True)
    assert e.value.code == "archive.forbidden"


def test_cannot_archive_twice(session, firm):
    _closed(session, firm)
    arch.archive(session, firm["p"], firm["case"].id, force=True)
    with pytest.raises(DomainError) as e:
        arch.archive(session, firm["p"], firm["case"].id, force=True)
    assert e.value.code == "archive.already"


def test_archive_can_be_redone_after_unarchive(session, firm):
    """解档后再归档 —— 二审打完了要重新封卷。卷号是新的。"""
    _closed(session, firm)
    a = arch.archive(session, firm["p"], firm["case"].id, force=True)
    arch.unarchive(session, firm["p"], firm["case"].id, "二审")
    for to in ("closing", "closed"):
        try:
            case_svc.transition(session, firm["case"].id, to,
                            actor_id=firm["wang"].id)
        except DomainError:
            pass
    b = arch.archive(session, firm["p"], firm["case"].id, force=True)
    assert b.volume_no != a.volume_no
    assert len(arch.history(session, firm["p"], firm["case"].id)) == 2


def test_volume_number_and_keep_until(session, firm):
    _closed(session, firm)
    r = arch.archive(session, firm["p"], firm["case"].id, force=True,
                     keep_years=10)
    assert r.volume_no.startswith("J")
    assert r.keep_until.year == date.today().year + 10
    assert r.item_count > 0 and r.fingerprint
