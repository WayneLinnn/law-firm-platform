"""利冲检查。

验证四件事：
  1. 双向检查 —— 对方是不是我们的人；我们要代理的人所里是不是有人在告
  2. 关联方展开 —— 只比名字查不出"对方是我们客户的全资子公司"
  3. 分级判定 —— blocked 自动阻断；review 必须人判；notice 只记录
  4. **卡住流程** —— 没过利冲不能进入待立案，这是执业红线
"""
import pytest
from sqlalchemy.orm import Session

from kernel.errors import DomainError
from modules.case import service as case_svc
from modules.conflict import service as conflict
from modules.crm import service as crm
from modules.identity import access
from modules.identity import service as identity


@pytest.fixture
def session():
    from kernel.db.session import get_engine

    try:
        conn = get_engine().connect()
    except Exception as exc:
        pytest.skip("数据库不可用：{0}".format(exc))
    tx = conn.begin()
    s = Session(bind=conn, expire_on_commit=False)
    try:
        yield s
    finally:
        s.close()
        tx.rollback()
        conn.close()


@pytest.fixture
def firm(session: Session):
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = identity.create_tenant(session, "cf-" + sfx, "康桥")
    identity.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in case_svc.ensure_default_causes(session, t.id)}

    root = identity.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    law = identity.create_org_unit(session, t.id, "律师", "l-" + sfx, parent_id=root.id)
    t1 = identity.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                  kind="team", parent_id=law.id)
    t2 = identity.create_org_unit(session, t.id, "李团队", "t2-" + sfx,
                                  kind="team", parent_id=law.id)

    def person(name, org):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, "senior_partner")
        identity.assign_membership(session, u.id, org.id, is_primary=True)
        access.assign_role(session, u.id, "committee")
        return u

    return dict(tenant=t, causes=causes, t1=t1, t2=t2, sfx=sfx,
                wang=person("wang", t1), li=person("li", t2))


def party(session, firm, name):
    return crm.create_party(session, firm["tenant"].id, name, allow_similar=True)


def make_case(session, firm, code, lead=None, org=None, cause="construction"):
    return case_svc.create_case(
        session, firm["tenant"].id, code, "案件 " + code,
        lead_id=(lead or firm["wang"]).id,
        cause_id=firm["causes"][cause].id,
        org_unit_id=(org or firm["t1"]).id,
    )


def activate(session, case_id):
    """把案子推到办理中（跳过利冲：这些是"既有案件"，用来构造冲突场景）。"""
    case_svc.transition(session, case_id, "conflict_check")
    check = conflict.run_check(session, case_id and case_svc.get_case(session, case_id).tenant_id,
                               case_id)
    if check.status not in conflict.PASSING_STATUSES:
        conflict.decide(session, check.id, "waived", 1, "测试构造场景")
    case_svc.transition(session, case_id, "pending")
    case_svc.transition(session, case_id, "active")


# ================================================================ 无冲突


def test_clean_case_passes(session, firm):
    a = party(session, firm, "甲公司")
    b = party(session, firm, "乙公司")
    c = make_case(session, firm, "N001")
    case_svc.add_party(session, firm["tenant"].id, c.id, a.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, b.id, "defendant")

    check = conflict.run_check(session, firm["tenant"].id, c.id, requested_by=firm["wang"].id)
    assert check.status == "cleared"
    assert conflict.hits_of(session, check.id) == []


def test_no_parties_cannot_check(session, firm):
    """没登记当事人就没法查 —— 报错而不是返回"通过"。"""
    c = make_case(session, firm, "N002")
    with pytest.raises(DomainError, match="没有登记当事人"):
        conflict.run_check(session, firm["tenant"].id, c.id)


# ================================================================ 直接冲突 ★


def test_opponent_is_our_client_blocks(session, firm):
    """★ 最典型的利冲：新案子的对方，正是我所客户。"""
    jia = party(session, firm, "甲公司")
    yi = party(session, firm, "乙公司")
    crm.create_client(session, firm["tenant"].id, jia.id, "KH001", owner_id=firm["wang"].id)

    c = make_case(session, firm, "D001")
    case_svc.add_party(session, firm["tenant"].id, c.id, yi.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, jia.id, "defendant")

    check = conflict.run_check(session, firm["tenant"].id, c.id)
    assert check.status == "blocked"
    hits = conflict.hits_of(session, check.id)
    assert hits[0].level == "blocked"
    assert hits[0].kind == "direct"
    assert "就是我所客户" in hits[0].description


def test_opponent_is_our_party_in_open_case_blocks(session, firm):
    """对方在所里另一个在办案子里是我方当事人 —— 阻断。"""
    jia = party(session, firm, "甲公司")
    yi = party(session, firm, "乙公司")
    bing = party(session, firm, "丙公司")

    old = make_case(session, firm, "D010")
    case_svc.add_party(session, firm["tenant"].id, old.id, jia.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, old.id, yi.id, "defendant")
    activate(session, old.id)

    new = make_case(session, firm, "D011")
    case_svc.add_party(session, firm["tenant"].id, new.id, bing.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, new.id, jia.id, "defendant")

    check = conflict.run_check(session, firm["tenant"].id, new.id)
    assert check.status == "blocked"
    assert any(h.kind == "direct" and h.related_case_id == old.id
               for h in conflict.hits_of(session, check.id))


# ================================================================ 关联方冲突 ★


def test_affiliate_of_client_needs_review(session, firm):
    """★ 对方是我所客户的子公司 —— 只比名字查不出来，必须顺着关联方查。"""
    jituan = party(session, firm, "甲集团")
    ziGongsi = party(session, firm, "甲科技")
    crm.relate_parties(session, firm["tenant"].id, jituan.id, ziGongsi.id, "subsidiary")
    crm.create_client(session, firm["tenant"].id, jituan.id, "KH010", owner_id=firm["wang"].id)

    ours = party(session, firm, "我方公司")
    c = make_case(session, firm, "A001")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, ziGongsi.id, "defendant")

    check = conflict.run_check(session, firm["tenant"].id, c.id)
    assert check.status == "pending", "关联方冲突必须交给人判，不能自动放行"
    hit = conflict.hits_of(session, check.id)[0]
    assert hit.level == "review"
    assert hit.kind == "affiliate"
    assert "关联方" in hit.description and "甲集团" in hit.description


def test_unrelated_party_does_not_trigger(session, firm):
    """没有关联关系就不该命中 —— 误报太多等于没有利冲检查。"""
    jia = party(session, firm, "甲集团")
    crm.create_client(session, firm["tenant"].id, jia.id, "KH011", owner_id=firm["wang"].id)
    unrelated = party(session, firm, "毫无关系公司")

    ours = party(session, firm, "我方公司")
    c = make_case(session, firm, "A002")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, unrelated.id, "defendant")

    assert conflict.run_check(session, firm["tenant"].id, c.id).status == "cleared"


# ================================================================ 所内对立 ★


def test_firm_already_against_our_new_client(session, firm):
    """★ 反方向：我们要代理的人，所里另一个团队正在告他。

    这种要么放弃，要么设隔离墙 —— 必须人判。
    """
    target = party(session, firm, "被告公司")
    someone = party(session, firm, "别的原告")

    old = make_case(session, firm, "F001", lead=firm["li"], org=firm["t2"])
    case_svc.add_party(session, firm["tenant"].id, old.id, someone.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, old.id, target.id, "defendant")
    activate(session, old.id)

    # 新案的对方要换个人 —— 若沿用旧案原告，那还叠加一层「对方曾是我方」的
    # 直接冲突，结论会是 blocked（也正确，但测不出 firm_side 这一档）
    fresh = party(session, firm, "新原告")
    new = make_case(session, firm, "F002", lead=firm["wang"], org=firm["t1"])
    case_svc.add_party(session, firm["tenant"].id, new.id, target.id, "defendant", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, new.id, fresh.id, "plaintiff")

    check = conflict.run_check(session, firm["tenant"].id, new.id)
    hits = conflict.hits_of(session, check.id)
    assert [h.kind for h in hits] == ["firm_side"]
    assert hits[0].level == "review"
    assert "所内已有团队站在对面" in hits[0].description
    assert check.status == "pending", "所内对立要人判，不能自动放行也不该自动阻断"


def test_double_conflict_escalates_to_blocked(session, firm):
    """两重冲突叠加时取最重的一档。

    旧案原告在新案里成了对方 —— 除了所内对立，还多一层「对方曾是我方当事人」，
    结论应当是阻断。
    """
    target = party(session, firm, "双重被告")
    someone = party(session, firm, "双重原告")

    old = make_case(session, firm, "F010", lead=firm["li"], org=firm["t2"])
    case_svc.add_party(session, firm["tenant"].id, old.id, someone.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, old.id, target.id, "defendant")
    activate(session, old.id)

    new = make_case(session, firm, "F011", lead=firm["wang"], org=firm["t1"])
    case_svc.add_party(session, firm["tenant"].id, new.id, target.id, "defendant", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, new.id, someone.id, "plaintiff")

    check = conflict.run_check(session, firm["tenant"].id, new.id)
    kinds = {h.kind for h in conflict.hits_of(session, check.id)}
    assert kinds == {"firm_side", "direct"}
    assert check.status == "blocked"


# ================================================================ 历史关系


def test_closed_case_is_only_a_notice(session, firm):
    """已结案的旧关系只提示不阻断 —— 否则老所几乎接不了新案子。"""
    jia = party(session, firm, "老客户公司")
    yi = party(session, firm, "老对手公司")

    old = make_case(session, firm, "H001")
    case_svc.add_party(session, firm["tenant"].id, old.id, jia.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, old.id, yi.id, "defendant")
    activate(session, old.id)
    for step in ("closing", "closed"):
        case_svc.transition(session, old.id, step)

    new = make_case(session, firm, "H002")
    case_svc.add_party(session, firm["tenant"].id, new.id, yi.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, new.id, jia.id, "defendant")

    check = conflict.run_check(session, firm["tenant"].id, new.id)
    hits = conflict.hits_of(session, check.id)
    assert all(h.level == "notice" for h in hits), [(h.level, h.kind) for h in hits]
    assert check.status == "cleared"


# ================================================================ 卡住流程 ★★


def test_cannot_open_case_without_check(session, firm):
    """★ 执业红线：没做利冲不能进入待立案。"""
    a = party(session, firm, "甲")
    c = make_case(session, firm, "G001")
    case_svc.add_party(session, firm["tenant"].id, c.id, a.id, "plaintiff", is_ours=True)
    case_svc.transition(session, c.id, "conflict_check")

    with pytest.raises(DomainError, match="尚未做利冲检查"):
        case_svc.transition(session, c.id, "pending")


def test_pending_hits_block_the_flow(session, firm):
    """命中未处置 → 不能往下走，报错里说清还剩几条。"""
    jituan = party(session, firm, "乙集团")
    zi = party(session, firm, "乙科技")
    crm.relate_parties(session, firm["tenant"].id, jituan.id, zi.id, "subsidiary")
    crm.create_client(session, firm["tenant"].id, jituan.id, "KH020", owner_id=firm["wang"].id)

    ours = party(session, firm, "我方")
    c = make_case(session, firm, "G002")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, zi.id, "defendant")
    case_svc.transition(session, c.id, "conflict_check")
    conflict.run_check(session, firm["tenant"].id, c.id)

    with pytest.raises(DomainError, match="条命中未处置"):
        case_svc.transition(session, c.id, "pending")


def test_blocked_case_can_never_open(session, firm):
    jia = party(session, firm, "客户甲")
    crm.create_client(session, firm["tenant"].id, jia.id, "KH030", owner_id=firm["wang"].id)
    ours = party(session, firm, "我方乙")

    c = make_case(session, firm, "G003")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, jia.id, "defendant")
    case_svc.transition(session, c.id, "conflict_check")
    conflict.run_check(session, firm["tenant"].id, c.id)

    with pytest.raises(DomainError, match="阻断"):
        case_svc.transition(session, c.id, "pending")


def test_waived_case_can_open(session, firm):
    """管委会放行后就能继续 —— 但理由必须写下来。"""
    jituan = party(session, firm, "丙集团")
    zi = party(session, firm, "丙科技")
    crm.relate_parties(session, firm["tenant"].id, jituan.id, zi.id, "subsidiary")
    crm.create_client(session, firm["tenant"].id, jituan.id, "KH040", owner_id=firm["wang"].id)

    ours = party(session, firm, "我方丙")
    c = make_case(session, firm, "G004")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, zi.id, "defendant")
    case_svc.transition(session, c.id, "conflict_check")
    check = conflict.run_check(session, firm["tenant"].id, c.id)

    conflict.decide(session, check.id, "waived", firm["wang"].id,
                    "两家业务无实质关联，客户已书面同意")
    case_svc.transition(session, c.id, "pending")
    assert c.status == "pending"


def test_waive_requires_a_reason(session, firm):
    """★ 放行必须写理由 —— 事后追责唯一能解释"为什么当时接了"的东西。"""
    jituan = party(session, firm, "丁集团")
    zi = party(session, firm, "丁科技")
    crm.relate_parties(session, firm["tenant"].id, jituan.id, zi.id, "subsidiary")
    crm.create_client(session, firm["tenant"].id, jituan.id, "KH050", owner_id=firm["wang"].id)

    ours = party(session, firm, "我方丁")
    c = make_case(session, firm, "G005")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, zi.id, "defendant")
    check = conflict.run_check(session, firm["tenant"].id, c.id)

    with pytest.raises(DomainError, match="必须写明理由"):
        conflict.decide(session, check.id, "waived", firm["wang"].id, "   ")


# ================================================================ 留档与隔离墙


def test_history_is_never_overwritten(session, firm):
    """补录当事人后要重查 —— 每次留一条，历史不覆盖。"""
    a = party(session, firm, "甲一")
    b = party(session, firm, "乙一")
    c = make_case(session, firm, "R001")
    case_svc.add_party(session, firm["tenant"].id, c.id, a.id, "plaintiff", is_ours=True)
    first = conflict.run_check(session, firm["tenant"].id, c.id)

    case_svc.add_party(session, firm["tenant"].id, c.id, b.id, "defendant")
    second = conflict.run_check(session, firm["tenant"].id, c.id)

    checks = conflict.checks_of_case(session, c.id)
    assert [x.id for x in checks] == [second.id, first.id]
    assert conflict.latest_check(session, c.id).id == second.id


def test_snapshot_records_what_was_checked(session, firm):
    """当事人后来改了，也要能还原当时查的是什么。"""
    import json

    a = party(session, firm, "快照甲")
    c = make_case(session, firm, "R002")
    case_svc.add_party(session, firm["tenant"].id, c.id, a.id, "plaintiff", is_ours=True)
    check = conflict.run_check(session, firm["tenant"].id, c.id)

    snap = json.loads(check.snapshot)
    assert snap[0]["name"] == "快照甲"
    assert snap[0]["is_ours"] is True


def test_waive_can_set_ethical_wall(session, firm):
    """放行时顺带设隔离墙 —— 优先级高于一切数据范围，主任自己也被挡。"""
    from modules.identity import access as ident_access

    jituan = party(session, firm, "戊集团")
    zi = party(session, firm, "戊科技")
    crm.relate_parties(session, firm["tenant"].id, jituan.id, zi.id, "subsidiary")
    crm.create_client(session, firm["tenant"].id, jituan.id, "KH060", owner_id=firm["wang"].id)

    ours = party(session, firm, "我方戊")
    c = make_case(session, firm, "W001")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, zi.id, "defendant")
    check = conflict.run_check(session, firm["tenant"].id, c.id)

    conflict.decide(session, check.id, "waived", firm["wang"].id, "设隔离墙后承接",
                    walls=[{"user_id": firm["li"].id, "reason": "利冲隔离"}])

    wall = ident_access.wall_view(session, firm["tenant"].id, firm["li"].id, "case")
    assert wall.blocks(str(c.id))
    # 没被隔离的人不受影响
    other = ident_access.wall_view(session, firm["tenant"].id, firm["wang"].id, "case")
    assert not other.blocks(str(c.id))


def test_hits_are_resolved_on_decision(session, firm):
    jituan = party(session, firm, "己集团")
    zi = party(session, firm, "己科技")
    crm.relate_parties(session, firm["tenant"].id, jituan.id, zi.id, "subsidiary")
    crm.create_client(session, firm["tenant"].id, jituan.id, "KH070", owner_id=firm["wang"].id)

    ours = party(session, firm, "我方己")
    c = make_case(session, firm, "W002")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, zi.id, "defendant")
    check = conflict.run_check(session, firm["tenant"].id, c.id)

    assert all(not h.resolved for h in conflict.hits_of(session, check.id))
    conflict.decide(session, check.id, "waived", firm["wang"].id, "管委会 2026 第 5 次会议")
    assert all(h.resolved for h in conflict.hits_of(session, check.id))


# ================================================================ 隔离墙必须真的生效 ★★


def test_wall_actually_blocks_case_access(session, firm):
    """★ 墙设了必须真的挡住 —— 单条访问和列表都要。

    这个 bug 真实发生过：kernel 有 apply_wall/assert_not_walled，
    identity 有 wall_view，但 case 从来没调过它们 ——
    墙"设好了"，访问却畅通无阻。设计和实现都在、就是没接上，
    这类漏洞比没做还危险，因为看起来像做了。
    """
    from kernel.errors import PermissionDenied
    from kernel.security import DataScope, Principal

    jituan = party(session, firm, "墙集团")
    zi = party(session, firm, "墙科技")
    crm.relate_parties(session, firm["tenant"].id, jituan.id, zi.id, "subsidiary")
    crm.create_client(session, firm["tenant"].id, jituan.id, "KH080", owner_id=firm["wang"].id)

    ours = party(session, firm, "我方墙")
    c = make_case(session, firm, "WALL1")
    case_svc.add_party(session, firm["tenant"].id, c.id, ours.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, zi.id, "defendant")
    check = conflict.run_check(session, firm["tenant"].id, c.id)
    conflict.decide(session, check.id, "waived", firm["wang"].id, "设墙后承接",
                    walls=[{"user_id": firm["li"].id, "reason": "利冲隔离"}])

    def principal_of(user):
        base = access.build_principal(session, user.id)
        return Principal(
            user_id=base.user_id, tenant_id=base.tenant_id,
            permissions=frozenset({"case.view"}), scopes={"case.view": DataScope.ALL},
            org_ids=base.org_ids, org_tree_ids=base.org_tree_ids,
        )

    li_p = principal_of(firm["li"])
    wang_p = principal_of(firm["wang"])

    # 单条：被墙的人访问要 403，且措辞不能暴露"这条被隔离了"
    with pytest.raises(PermissionDenied) as exc:
        case_svc.assert_visible(session, li_p, c.id)
    assert "隔离" not in str(exc.value)
    # 没被墙的人照常
    assert case_svc.assert_visible(session, wang_p, c.id).id == c.id

    # 列表：墙住的案子不出现在结果里，即使他是 ALL 范围
    li_codes = set(session.scalars(case_svc.scoped_cases(session, li_p).with_only_columns(
        __import__("modules.case.models", fromlist=["Case"]).Case.code)))
    assert "WALL1" not in li_codes
    wang_codes = set(session.scalars(case_svc.scoped_cases(session, wang_p).with_only_columns(
        __import__("modules.case.models", fromlist=["Case"]).Case.code)))
    assert "WALL1" in wang_codes


# ================================================================ 分级处置


def _checked(session, firm, code):
    """造一个有命中的检查 —— 双方都挂上，跑一次。"""
    a = party(session, firm, "甲公司" + code)
    b = party(session, firm, "乙公司" + code)
    c = make_case(session, firm, code)
    case_svc.add_party(session, firm["tenant"].id, c.id, a.id, "plaintiff", is_ours=True)
    case_svc.add_party(session, firm["tenant"].id, c.id, b.id, "defendant")
    check = conflict.run_check(session, firm["tenant"].id, c.id,
                               requested_by=firm["wang"].id)
    return c, check


def test_who_decides_depends_on_hit_level(session, firm):
    """★ **谁能拍板看最高命中等级，不是一刀切给管委会。**

    原来只有一档 `conflict.decide`：所有命中都推给管委会。
    问题是**管委会在中国律所不是天天在线的审批岗，是开会的机构** ——
    把"关联方在别的案子里出现过"也推给它，它待办里就堆满本该秒过的东西，
    然后养成"全点放行"的习惯，**这道闸就废了**。
    """
    _c, check = _checked(session, firm, "L001")
    hits = conflict.hits_of(session, check.id)
    if not hits:
        pytest.skip("这组数据没命中，测不到分级")

    for h in hits:
        h.level = "review"
    session.flush()
    assert conflict.required_perm(session, check.id) == "conflict.review"

    hits[0].level = "blocked"
    session.flush()
    assert conflict.required_perm(session, check.id) == "conflict.decide"


def test_decide_is_a_superset_of_review(session, firm):
    """★ 有 decide 的人自然也能处理 review —— 权限是包含关系。

    反过来不行：真冲突必须上管委会，这是执业红线。
    """
    from modules.identity import access

    _c, check = _checked(session, firm, "L002")
    hits = conflict.hits_of(session, check.id)
    if not hits:
        pytest.skip("这组数据没命中")
    for h in hits:
        h.level = "review"
    session.flush()

    p = access.build_principal(session, firm["wang"].id)
    object.__setattr__(p, "permissions",
                       frozenset(set(p.permissions) | {"conflict.decide"}))
    conflict.assert_can_decide(session, p, check.id)   # 不该抛


def test_error_says_who_to_ask(session, firm):
    """★ 报错要说清**该找谁**，不能只说"你没权限"。"""
    from modules.identity import access

    _c, check = _checked(session, firm, "L003")
    hits = conflict.hits_of(session, check.id)
    if not hits:
        pytest.skip("这组数据没命中")
    for h in hits:
        h.level = "blocked"
    session.flush()

    p = access.build_principal(session, firm["wang"].id)
    object.__setattr__(p, "permissions",
                       frozenset(x for x in p.permissions
                                 if x != "conflict.decide"))
    with pytest.raises(DomainError) as e:
        conflict.assert_can_decide(session, p, check.id)
    assert e.value.code == "conflict.cannot_decide"
    assert "管委会" in e.value.message


def test_passing_a_check_advances_the_case(session, firm):
    """★★ **利冲放行之后要把案件往前推。**

    这是踩出来的：用户跑完利冲、点了放行、办案流程也走完了，
    案件却一直显示「草稿」—— 因为**利冲、流程、案件状态是三条
    互不相通的线**。人得再去案件概览手动点两次。

    **做完了事却要去别处再点一遍确认，人一定会漏** ——
    然后案件卡在草稿，待办、看板、时限全都不把它当在办案件。

    ★ 只推到「待立案」，**不自动推到「办理中」** ——
      立没立案是法院说了算的事实，不是系统能替律师断的。
    """
    c, check = _checked(session, firm, "L004")

    # ★ 两条路都要推：无冲突（cleared）直接推，有命中的放行后推。
    #   `cleared` 那条尤其重要 —— 它连"处置"这个动作都没有
    #   （decide 会拒"本次检查无冲突，无需处置"），
    #   **最顺利的那条路反而最容易卡住**。
    if check.status != "cleared":
        conflict.decide(session, check.id, "waived", firm["wang"].id, "测试放行")
    session.flush()

    assert case_svc.get_case(session, c.id).status == "pending"

    logs = case_svc.status_history(session, c.id)
    assert any("自动推进" in (l.note or "") for l in logs)
