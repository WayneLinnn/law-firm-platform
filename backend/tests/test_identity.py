"""组织与身份。

跑真实 Postgres，每个用例在事务里跑完回滚，互不污染。
数据库不可用则整体 skip（CI 里没有库也不会红）。
"""
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound, PermissionDenied, Unauthenticated
from modules.identity import service


@pytest.fixture
def session():
    from kernel.db.session import get_engine

    try:
        engine = get_engine()
        conn = engine.connect()
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
    """一个典型律所：所 → 两个部门 → 一个项目组。"""
    import uuid

    suffix = uuid.uuid4().hex[:8]
    tenant = service.create_tenant(session, "firm-" + suffix, "康桥律师事务所")
    root = service.create_org_unit(session, tenant.id, "康桥律所", "root-" + suffix, kind="firm")
    litigation = service.create_org_unit(
        session, tenant.id, "诉讼一部", "lit1-" + suffix, parent_id=root.id
    )
    nonlit = service.create_org_unit(
        session, tenant.id, "非诉部", "nonlit-" + suffix, parent_id=root.id
    )
    project = service.create_org_unit(
        session, tenant.id, "XX并购项目组", "proj-" + suffix,
        kind="team", parent_id=nonlit.id,
    )
    return {
        "tenant": tenant, "root": root, "litigation": litigation,
        "nonlit": nonlit, "project": project, "suffix": suffix,
    }


# ================================================================ 组织树


def test_tree_is_free_form(session, firm):
    """层级不写死：所→部门→团队，几层都行。"""
    units = service.list_org_units(session, firm["tenant"].id)
    assert len(units) == 4
    assert firm["project"].parent_id == firm["nonlit"].id


def test_descendant_ids_covers_whole_subtree(session, firm):
    """Step 4 的 DEPT_TREE 数据范围靠它。"""
    ids = service.descendant_ids(session, firm["root"].id)
    assert set(ids) == {
        firm["root"].id, firm["litigation"].id, firm["nonlit"].id, firm["project"].id
    }
    assert service.descendant_ids(session, firm["litigation"].id) == [firm["litigation"].id]


def test_move_org_unit(session, firm):
    """部门改挂靠是常见操作，必须支持。"""
    service.move_org_unit(session, firm["project"].id, firm["litigation"].id)
    assert firm["project"].parent_id == firm["litigation"].id
    assert firm["project"].id in service.descendant_ids(session, firm["litigation"].id)


def test_move_rejects_cycle(session, firm):
    with pytest.raises(DomainError, match="后代"):
        service.move_org_unit(session, firm["nonlit"].id, firm["project"].id)
    with pytest.raises(DomainError, match="自己"):
        service.move_org_unit(session, firm["nonlit"].id, firm["nonlit"].id)


# ================================================================ 一人多归属 ★


def test_one_person_belongs_to_multiple_orgs(session, firm):
    """律所是矩阵结构：常设部门 + 临时项目组，同时生效。

    这是 Step 3 最重要的一条 —— 如果把部门做成 user 表上的一个字段，
    将来改多归属要翻遍所有权限查询、报表、分配逻辑。
    """
    zhang = service.create_user(
        session, firm["tenant"].id, "zhang" + firm["suffix"], "张三", "password123"
    )
    service.assign_membership(session, zhang.id, firm["litigation"].id,
                              org_role="组员", is_primary=True)
    service.assign_membership(session, zhang.id, firm["project"].id, org_role="组员")

    ms = service.memberships_of(session, zhang.id)
    assert len(ms) == 2
    assert ms[0].is_primary is True          # 主归属排在最前
    assert {m.org_unit_id for m in ms} == {firm["litigation"].id, firm["project"].id}


def test_only_one_primary_at_a_time(session, firm):
    """统计口径需要唯一主归属。"""
    li = service.create_user(session, firm["tenant"].id, "li" + firm["suffix"], "李四", "password123")
    service.assign_membership(session, li.id, firm["litigation"].id, is_primary=True)
    service.assign_membership(session, li.id, firm["nonlit"].id, is_primary=True)

    primaries = [m for m in service.memberships_of(session, li.id) if m.is_primary]
    assert len(primaries) == 1
    assert primaries[0].org_unit_id == firm["nonlit"].id


def test_duplicate_membership_rejected(session, firm):
    w = service.create_user(session, firm["tenant"].id, "wang" + firm["suffix"], "王五", "password123")
    service.assign_membership(session, w.id, firm["litigation"].id)
    with pytest.raises(DomainError, match="已有生效归属"):
        service.assign_membership(session, w.id, firm["litigation"].id)


# ================================================================ 有效期 ★


def test_transfer_keeps_history(session, firm):
    """调岗不是删记录，而是关闭时间区间。

    张三 3 月从诉讼一部调到非诉部；查 1 月他仍在诉讼一部 ——
    否则 1 月立的案子在部门统计里会算错。
    """
    zhao = service.create_user(session, firm["tenant"].id, "zhao" + firm["suffix"], "赵六", "password123")
    jan, mar, jun = date(2026, 1, 1), date(2026, 3, 1), date(2026, 6, 1)

    old = service.assign_membership(session, zhao.id, firm["litigation"].id, valid_from=jan)
    service.end_membership(session, old.id, end_on=mar)
    service.assign_membership(session, zhao.id, firm["nonlit"].id, valid_from=mar)

    at_jan = service.memberships_of(session, zhao.id, at=date(2026, 1, 15))
    assert [m.org_unit_id for m in at_jan] == [firm["litigation"].id]

    at_jun = service.memberships_of(session, zhao.id, at=jun)
    assert [m.org_unit_id for m in at_jun] == [firm["nonlit"].id]


def test_members_of_respects_point_in_time(session, firm):
    sun = service.create_user(session, firm["tenant"].id, "sun" + firm["suffix"], "孙七", "password123")
    start = date.today() - timedelta(days=30)
    m = service.assign_membership(session, sun.id, firm["litigation"].id, valid_from=start)
    service.end_membership(session, m.id, end_on=date.today() - timedelta(days=10))

    assert service.members_of(session, firm["litigation"].id) == []
    past = service.members_of(session, firm["litigation"].id, at=date.today() - timedelta(days=20))
    assert [x.user_id for x in past] == [sun.id]


def test_end_date_before_start_rejected(session, firm):
    u = service.create_user(session, firm["tenant"].id, "zhou" + firm["suffix"], "周八", "password123")
    m = service.assign_membership(session, u.id, firm["litigation"].id, valid_from=date(2026, 5, 1))
    with pytest.raises(DomainError, match="不能早于"):
        service.end_membership(session, m.id, end_on=date(2026, 4, 1))


def test_members_of_can_include_subtree(session, firm):
    """查"非诉部及其下级"的所有人。"""
    u = service.create_user(session, firm["tenant"].id, "wu" + firm["suffix"], "吴九", "password123")
    service.assign_membership(session, u.id, firm["project"].id)

    assert service.members_of(session, firm["nonlit"].id) == []
    rows = service.members_of(session, firm["nonlit"].id, include_descendants=True)
    assert [x.user_id for x in rows] == [u.id]


def test_org_ids_of_user_for_datascope(session, firm):
    """Step 4 的 DataScope 会用这个函数决定"能看到哪些组织"。"""
    u = service.create_user(session, firm["tenant"].id, "zheng" + firm["suffix"], "郑十", "password123")
    service.assign_membership(session, u.id, firm["nonlit"].id)

    assert service.org_ids_of_user(session, u.id) == [firm["nonlit"].id]
    with_tree = service.org_ids_of_user(session, u.id, include_descendants=True)
    assert set(with_tree) == {firm["nonlit"].id, firm["project"].id}


# ================================================================ 认证


def test_password_is_hashed_not_stored(session, firm):
    u = service.create_user(session, firm["tenant"].id, "sec" + firm["suffix"], "安全", "password123")
    assert u.password_hash != "password123"
    assert service.verify_password("password123", u.password_hash)
    assert not service.verify_password("wrong", u.password_hash)


def test_weak_password_rejected(session, firm):
    with pytest.raises(DomainError, match="至少 8 位"):
        service.create_user(session, firm["tenant"].id, "weak" + firm["suffix"], "弱", "123")


def test_duplicate_username_rejected(session, firm):
    service.create_user(session, firm["tenant"].id, "dup" + firm["suffix"], "甲", "password123")
    with pytest.raises(DomainError, match="已存在"):
        service.create_user(session, firm["tenant"].id, "dup" + firm["suffix"], "乙", "password123")


def test_authenticate_and_token_roundtrip(session, firm):
    u = service.create_user(session, firm["tenant"].id, "auth" + firm["suffix"], "认证", "password123")
    got = service.authenticate(session, firm["tenant"].code, u.username, "password123")
    assert got.id == u.id

    payload = service.decode_token(service.issue_token(u))
    assert payload["sub"] == str(u.id)
    assert payload["tenant"] == firm["tenant"].id


def test_bad_credentials_do_not_leak_which_part_is_wrong(session, firm):
    """不区分"律所不存在"和"密码错误"，避免被探测。"""
    service.create_user(session, firm["tenant"].id, "probe" + firm["suffix"], "探测", "password123")
    with pytest.raises(PermissionDenied, match="用户名或密码错误"):
        service.authenticate(session, firm["tenant"].code, "probe" + firm["suffix"], "wrong")
    with pytest.raises(PermissionDenied, match="用户名或密码错误"):
        service.authenticate(session, "no-such-firm", "probe" + firm["suffix"], "password123")


def test_invalid_token_rejected(session):
    with pytest.raises(Unauthenticated, match="无效"):
        service.decode_token("not.a.token")


# ================================================================ 租户隔离


def test_cannot_assign_across_tenants(session, firm):
    import uuid

    other_code = "other-" + uuid.uuid4().hex[:8]
    other = service.create_tenant(session, other_code, "别家律所")
    outsider = service.create_user(session, other.id, "out" + firm["suffix"], "外人", "password123")
    with pytest.raises(DomainError, match="同一律所"):
        service.assign_membership(session, outsider.id, firm["litigation"].id)


# ================================================================ 职级与晋升 ★


@pytest.fixture
def positions(session: Session, firm):
    return {p.code: p for p in service.ensure_default_positions(session, firm["tenant"].id)}


def test_default_positions_match_firm_reality(session, firm, positions):
    """律所的四档律师职级 + 行政 + 财务。"""
    assert positions["senior_partner"].name == "高级合伙人"
    assert positions["partner"].name == "合伙人"
    assert positions["associate"].name == "独立律师"
    assert positions["paralegal"].name == "律师助理"

    # 每个属性都驱动真实业务逻辑，不是标签
    # 牵头团队是职级表上的开关，不是硬规则：高伙/合伙人/独立律师默认都可以
    assert positions["senior_partner"].can_lead_team is True
    assert positions["partner"].can_lead_team is True
    assert positions["associate"].can_lead_team is True
    assert positions["paralegal"].can_lead_team is False
    assert positions["partner"].is_partner is True
    assert positions["associate"].is_partner is False
    assert positions["paralegal"].can_handle_case is False       # 助理不能独立承办
    assert positions["paralegal"].can_bill is True               # 但工时要计入
    assert positions["admin"].can_bill is False
    assert positions["finance"].category == "finance"

    ranked = service.list_positions(session, firm["tenant"].id)
    assert ranked[0].code == "senior_partner"                    # 按级别排序


def test_positions_are_data_not_code(session, firm, positions):
    """律所可自定义职级 —— 加一档不用改代码。"""
    p = service.create_position(
        session, firm["tenant"].id, "counsel", "顾问律师",
        category="lawyer", rank=70, can_bill=True, can_handle_case=True,
    )
    codes = [x.code for x in service.list_positions(session, firm["tenant"].id)]
    assert "counsel" in codes
    assert codes.index("counsel") == 2      # rank 70，排在合伙人(80)与独立律师(60)之间


def test_promotion_keeps_history(session, firm, positions):
    """晋升不是改字段，是关旧区间开新区间。

    张三 2026-06 从独立律师升为合伙人；查 5 月他仍是独立律师 ——
    否则 5 月的案件分配会按合伙人重算，账就错了。
    """
    u = service.create_user(session, firm["tenant"].id, "promo" + firm["suffix"], "张三", "password123")
    service.appoint(session, u.id, "associate", valid_from=date(2024, 1, 1))
    service.appoint(session, u.id, "partner", valid_from=date(2026, 6, 1), note="管委会2026年第3次会议")

    assert service.position_of(session, u.id, at=date(2026, 5, 1)).code == "associate"
    assert service.position_of(session, u.id, at=date(2026, 7, 1)).code == "partner"

    history = service.appointment_history(session, u.id)
    assert [a.position_id for a in history] == [positions["partner"].id, positions["associate"].id]
    assert history[1].valid_to == date(2026, 6, 1)     # 旧区间被关闭
    assert history[0].note.startswith("管委会")


def test_cannot_appoint_same_position_twice(session, firm, positions):
    u = service.create_user(session, firm["tenant"].id, "same" + firm["suffix"], "李四", "password123")
    service.appoint(session, u.id, "partner")
    with pytest.raises(DomainError, match="已是此职级"):
        service.appoint(session, u.id, "partner")


# ================================================================ 团队规则 ★


def test_team_leader_follows_position_switch(session, firm, positions):
    """能否牵头团队由职级表的开关决定 —— 高伙、合伙人、独立律师默认都可以。"""
    for i, code in enumerate(("senior_partner", "partner", "associate")):
        team = service.create_org_unit(
            session, firm["tenant"].id, "团队%d" % i, "tm%d-%s" % (i, firm["suffix"]),
            kind="team", parent_id=firm["nonlit"].id,
        )
        u = service.create_user(session, firm["tenant"].id,
                                "ld%d%s" % (i, firm["suffix"]), "负责人%d" % i, "password123")
        service.appoint(session, u.id, code)
        m = service.assign_membership(session, u.id, team.id, org_role="负责人")
        assert service.leader_of(session, team.id).id == m.id


def test_paralegal_cannot_lead_by_default(session, firm, positions):
    """助理默认不能牵头 —— 但这是数据，律所改一行开关就能放开。"""
    team = service.create_org_unit(
        session, firm["tenant"].id, "助理团队", "pt-" + firm["suffix"],
        kind="team", parent_id=firm["nonlit"].id,
    )
    u = service.create_user(session, firm["tenant"].id, "pl" + firm["suffix"], "助理甲", "password123")
    service.appoint(session, u.id, "paralegal")
    with pytest.raises(DomainError, match="可牵头团队"):
        service.assign_membership(session, u.id, team.id, org_role="负责人")

    # 律所放开开关后立刻生效，无需改代码
    positions["paralegal"].can_lead_team = True
    session.flush()
    m = service.assign_membership(session, u.id, team.id, org_role="负责人")
    assert service.leader_of(session, team.id).id == m.id


def test_undated_user_cannot_lead_team(session, firm, positions):
    team = service.create_org_unit(
        session, firm["tenant"].id, "无主团队", "t2-" + firm["suffix"],
        kind="team", parent_id=firm["nonlit"].id,
    )
    u = service.create_user(session, firm["tenant"].id, "nod" + firm["suffix"], "未定级", "password123")
    with pytest.raises(DomainError, match="尚未定级"):
        service.assign_membership(session, u.id, team.id, org_role="负责人")


def test_teams_of_user(session, firm, positions):
    """律师基本都在团队里运作，且可同时在多个团队。"""
    team = service.create_org_unit(
        session, firm["tenant"].id, "陈XX团队", "t3-" + firm["suffix"],
        kind="team", parent_id=firm["nonlit"].id,
    )
    u = service.create_user(session, firm["tenant"].id, "tm" + firm["suffix"], "组员", "password123")
    service.appoint(session, u.id, "associate")
    service.assign_membership(session, u.id, firm["nonlit"].id, org_role="", is_primary=True)
    service.assign_membership(session, u.id, team.id, org_role="组员")

    teams = service.teams_of(session, u.id)
    assert [t.id for t in teams] == [team.id]      # 只返回团队，不含部门


# ================================================================ 管委会 ★


def test_committee_is_power_body_not_business_unit(session, firm, positions):
    """管委会与业务部门平行，成员即主任，是决定人员出入与晋升的权力机构。"""
    committee = service.create_org_unit(
        session, firm["tenant"].id, "管理委员会", "cmt-" + firm["suffix"],
        kind="committee", parent_id=firm["root"].id,
    )
    director = service.create_user(session, firm["tenant"].id, "dir" + firm["suffix"], "王主任", "password123")
    service.appoint(session, director.id, "senior_partner")
    service.assign_membership(session, director.id, committee.id, org_role="主任")

    outsider = service.create_user(session, firm["tenant"].id, "out2" + firm["suffix"], "普通律师", "password123")
    service.appoint(session, outsider.id, "associate")

    assert service.is_committee_member(session, director.id) is True
    assert service.is_committee_member(session, outsider.id) is False
    assert [m.user_id for m in service.committee_members(session, firm["tenant"].id)] == [director.id]


def test_role_and_position_are_independent(session, firm, positions):
    """职级跟人走，角色跟组织走 —— 同一个人在不同组织可以有不同角色。"""
    team = service.create_org_unit(
        session, firm["tenant"].id, "赵XX团队", "t4-" + firm["suffix"],
        kind="team", parent_id=firm["nonlit"].id,
    )
    committee = service.create_org_unit(
        session, firm["tenant"].id, "管委会2", "cmt2-" + firm["suffix"],
        kind="committee", parent_id=firm["root"].id,
    )
    u = service.create_user(session, firm["tenant"].id, "both" + firm["suffix"], "赵高伙", "password123")
    service.appoint(session, u.id, "senior_partner")
    service.assign_membership(session, u.id, team.id, org_role="负责人", is_primary=True)
    service.assign_membership(session, u.id, committee.id, org_role="主任")

    assert service.position_of(session, u.id).code == "senior_partner"     # 职级只有一个
    roles = {m.org_role for m in service.memberships_of(session, u.id)}
    assert roles == {"负责人", "主任"}                                      # 角色有多个


def test_stale_token_is_401_not_404(session, firm):
    """token 指向的用户已不存在（库重建、账号删除）→ 凭证失效，不是资源不存在。

    返回 404 的话前端不会跳登录页，会卡在空白页 —— 这个 bug 真实发生过。
    """
    import jwt as _jwt
    from datetime import datetime, timedelta, timezone

    from kernel.config import settings
    from kernel.errors import NotFound

    token = _jwt.encode(
        {"sub": "999999", "tenant": firm["tenant"].id, "username": "ghost",
         "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    payload = service.decode_token(token)
    with pytest.raises(NotFound):
        service.get_user(session, int(payload["sub"]))   # service 层仍是 404

    # 但认证依赖必须转成 401
    from modules.identity.deps import _user_from_token
    from kernel.errors import Unauthenticated

    with pytest.raises(Unauthenticated, match="凭证已失效"):
        _user_from_token(session, token)


def test_transfer_day_belongs_to_new_team_only(session, firm):
    """★ 调岗当天只属于新团队，不能新旧并存。

    时间区间是**半开** [valid_from, valid_to)：valid_to 是"失效日"不是"最后一天"。
    用闭区间的话，调岗当天这个人会同时出现在两个团队里 ——
    部门统计、权限范围、工时归集全部重复计算。
    """
    u = service.create_user(session, firm["tenant"].id, "bd" + firm["suffix"], "边界", "password123")
    day = date(2026, 4, 1)

    old = service.assign_membership(session, u.id, firm["litigation"].id,
                                    valid_from=date(2026, 1, 1))
    service.end_membership(session, old.id, end_on=day)
    service.assign_membership(session, u.id, firm["nonlit"].id, valid_from=day)

    on_day = service.memberships_of(session, u.id, at=day)
    assert len(on_day) == 1, "调岗当天出现了 %d 条归属" % len(on_day)
    assert on_day[0].org_unit_id == firm["nonlit"].id

    day_before = service.memberships_of(session, u.id, at=date(2026, 3, 31))
    assert [m.org_unit_id for m in day_before] == [firm["litigation"].id]


def test_promotion_day_uses_new_position_only(session, firm, positions):
    """晋升当天同理：只算新职级。"""
    u = service.create_user(session, firm["tenant"].id, "pb" + firm["suffix"], "晋升边界", "password123")
    day = date(2026, 7, 1)
    service.appoint(session, u.id, "associate", valid_from=date(2024, 1, 1))
    service.appoint(session, u.id, "partner", valid_from=day)

    assert service.position_of(session, u.id, at=date(2026, 6, 30)).code == "associate"
    assert service.position_of(session, u.id, at=day).code == "partner"
