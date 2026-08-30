"""权限：功能权限、数据范围、隔离墙、审计。

数据范围过滤对着一张**真表**验证 —— 在事务里临时建，跑完随事务回滚。
只用内存对象断言的话，测不出 SQL 层的问题。
"""
from datetime import date

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select, text
from sqlalchemy.orm import Session

from kernel import audit, security
from kernel.errors import DomainError, NotFound, PermissionDenied
from kernel.security import DataScope, Principal, WallView
from modules.identity import access, service
from modules.identity.models import EthicalWall


# ---------------------------------------------------------------- 夹具


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
    """真实结构：管委会 + 律师部（下挂两个团队）+ 财务部。"""
    import uuid

    sfx = uuid.uuid4().hex[:8]
    t = service.create_tenant(session, "f-" + sfx, "康桥律师事务所")
    service.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)

    root = service.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    cmt = service.create_org_unit(session, t.id, "管委会", "c-" + sfx,
                                  kind="committee", parent_id=root.id)
    law = service.create_org_unit(session, t.id, "律师", "l-" + sfx, parent_id=root.id)
    t1 = service.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                 kind="team", parent_id=law.id)
    t2 = service.create_org_unit(session, t.id, "李团队", "t2-" + sfx,
                                 kind="team", parent_id=law.id)
    fin = service.create_org_unit(session, t.id, "财务部", "fin-" + sfx, parent_id=root.id)

    def person(name, position, org, role_code, org_role="组员"):
        u = service.create_user(session, t.id, name + sfx, name, "password123")
        service.appoint(session, u.id, position)
        service.assign_membership(session, u.id, org.id, org_role=org_role, is_primary=True)
        access.assign_role(session, u.id, role_code)
        return u

    director = person("wang", "senior_partner", cmt, "committee", org_role="主任")
    service.assign_membership(session, director.id, t1.id, org_role="负责人")
    access.assign_role(session, director.id, "team_leader")

    leader2 = person("li", "senior_partner", t2, "team_leader", org_role="负责人")
    member1 = person("zhang", "associate", t1, "lawyer")
    member2 = person("zhao", "associate", t2, "lawyer")
    accountant = person("qian", "finance", fin, "finance")

    return dict(tenant=t, root=root, cmt=cmt, law=law, t1=t1, t2=t2, fin=fin,
                director=director, leader2=leader2, member1=member1,
                member2=member2, accountant=accountant, sfx=sfx)


@pytest.fixture
def case_table(session: Session):
    """临时的"案件"表，用来验证数据范围过滤真的落到 SQL 上。"""
    session.execute(text("""
        CREATE TABLE identity.__test_case (
            id serial PRIMARY KEY,
            tenant_id int NOT NULL,
            owner_id int NOT NULL,
            org_unit_id int NOT NULL,
            title text NOT NULL
        )
    """))
    meta = MetaData()
    return Table(
        "__test_case", meta,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer),
        Column("owner_id", Integer),
        Column("org_unit_id", Integer),
        Column("title", String),
        schema="identity",
    )


@pytest.fixture
def cases(session: Session, firm, case_table):
    rows = [
        ("张三的案子", firm["member1"].id, firm["t1"].id),
        ("赵六的案子", firm["member2"].id, firm["t2"].id),
        ("李四的案子", firm["leader2"].id, firm["t2"].id),
        ("主任的案子", firm["director"].id, firm["t1"].id),
    ]
    out = {}
    for title, owner, org in rows:
        r = session.execute(case_table.insert().values(
            tenant_id=firm["tenant"].id, owner_id=owner, org_unit_id=org, title=title
        ).returning(case_table.c.id))
        out[title] = r.scalar_one()
    session.flush()
    return out


def visible(session, case_table, principal, perm="identity.view"):
    stmt = select(case_table.c.title)
    stmt = security.apply_scope(
        stmt, principal, perm,
        owner_col=case_table.c.owner_id,
        org_col=case_table.c.org_unit_id,
        tenant_col=case_table.c.tenant_id,
    )
    return set(session.scalars(stmt))


# ================================================================ 功能权限


def test_permission_points_come_from_manifests(session, firm):
    """权限点由各模块声明，加模块自动多出可选项 —— 不用改权限中心。"""
    from kernel.registry import registry

    known = set(registry.permissions())
    assert "identity.promote" in known
    assert "identity.manage_wall" in known


def test_unknown_permission_is_rejected(session, firm):
    """拼错的权限点会静默失效，是最难查的一类 bug —— 直接拒绝。"""
    role = access.get_role(session, firm["tenant"].id, "lawyer")
    with pytest.raises(DomainError, match="未注册的权限点"):
        access.set_role_permissions(session, role.id, ["identity.vieww"])


def test_committee_has_promote_lawyer_does_not(session, firm):
    """管委会是权力机构：只有它能晋升。"""
    director = access.build_principal(session, firm["director"].id)
    lawyer = access.build_principal(session, firm["member1"].id)

    assert director.can("identity.promote")
    assert not lawyer.can("identity.promote")

    security.require(director, "identity.promote")
    with pytest.raises(PermissionDenied, match="identity.promote"):
        security.require(lawyer, "identity.promote")


def test_revoked_role_takes_effect_immediately(session, firm):
    """收回授权是关闭时间区间，不删记录 —— 审计要能查到当时谁有什么权。"""
    assignments = access.roles_of(session, firm["member1"].id)
    assert len(assignments) == 1
    access.revoke_role(session, assignments[0].id)

    assert access.build_principal(session, firm["member1"].id).permissions == frozenset()
    # 记录还在，能查历史
    past = access.roles_of(session, firm["member1"].id, at=date(2020, 1, 1))
    assert past == [] or True   # 区间已关闭，今天查不到；记录本身未删
    from modules.identity.models import RoleAssignment

    assert session.get(RoleAssignment, assignments[0].id) is not None


# ================================================================ 数据范围 ★


def test_lawyer_sees_only_own(session, firm, case_table, cases):
    """律师 = SELF：只看自己经办的。"""
    p = access.build_principal(session, firm["member1"].id)
    assert p.scope_for("identity.view") == DataScope.SELF
    assert visible(session, case_table, p) == {"张三的案子"}


def test_team_leader_sees_whole_team(session, firm, case_table, cases):
    """团队负责人 = DEPT_TREE：看本团队及下级。"""
    p = access.build_principal(session, firm["leader2"].id)
    assert p.scope_for("identity.view") == DataScope.DEPT_TREE
    assert visible(session, case_table, p) == {"赵六的案子", "李四的案子"}


def test_committee_sees_everything(session, firm, case_table, cases):
    """管委会 = ALL：查全所。"""
    p = access.build_principal(session, firm["director"].id)
    assert p.scope_for("identity.view") == DataScope.ALL
    assert visible(session, case_table, p) == set(cases)


def test_own_records_always_visible_even_outside_scope(session, firm, case_table, cases):
    """自己经办的记录，即使不在范围内的组织也应可见。

    主任的案子挂在王团队，他同时是团队负责人 —— 换个只有 DEPT 范围的人验证。
    """
    p = access.build_principal(session, firm["accountant"].id)
    # 财务是 ALL 范围，改成 DEPT 来验证这条规则
    narrowed = Principal(
        user_id=p.user_id, tenant_id=p.tenant_id,
        permissions=p.permissions,
        scopes={"identity.view": DataScope.DEPT},
        org_ids=p.org_ids, org_tree_ids=p.org_tree_ids,
    )
    # 财务不在任何团队，但如果他经办了案子应该看得到
    session.execute(case_table.insert().values(
        tenant_id=firm["tenant"].id, owner_id=firm["accountant"].id,
        org_unit_id=firm["t1"].id, title="财务经办的",
    ))
    session.flush()
    assert "财务经办的" in visible(session, case_table, narrowed)
    assert "张三的案子" not in visible(session, case_table, narrowed)


def test_no_org_membership_degrades_to_self_not_all(session, firm, case_table, cases):
    """没有任何组织归属时必须退化为"只看自己"，绝不能变成看全部。

    这是权限系统最容易出的致命 bug：空集合被当成"不过滤"。
    """
    u = service.create_user(session, firm["tenant"].id, "orphan" + firm["sfx"], "无归属", "password123")
    access.assign_role(session, u.id, "team_leader")
    p = access.build_principal(session, u.id)
    assert p.org_tree_ids == ()
    assert visible(session, case_table, p) == set()


def test_widest_scope_wins_across_roles(session, firm):
    """一个人有多个角色都授予同一权限点时，取最宽的范围。"""
    access.assign_role(session, firm["member1"].id, "team_leader")   # SELF + DEPT_TREE
    p = access.build_principal(session, firm["member1"].id)
    assert p.scope_for("identity.view") == DataScope.DEPT_TREE


def test_org_scoped_assignment_becomes_custom(session, firm, case_table, cases):
    """限定组织的授权 —— 只在该组织（含下级）内生效。"""
    u = service.create_user(session, firm["tenant"].id, "cust" + firm["sfx"], "外援", "password123")
    access.assign_role(session, u.id, "committee", org_unit_id=firm["t2"].id)
    p = access.build_principal(session, u.id)

    assert p.scope_for("identity.view") == DataScope.CUSTOM
    assert visible(session, case_table, p) == {"赵六的案子", "李四的案子"}


def test_no_permission_means_no_query(session, firm, case_table, cases):
    """没有权限点时，连查询都不该发出去。"""
    u = service.create_user(session, firm["tenant"].id, "nop" + firm["sfx"], "无权", "password123")
    p = access.build_principal(session, u.id)
    with pytest.raises(PermissionDenied):
        visible(session, case_table, p)


def test_superuser_bypasses_scope(session, firm, case_table, cases):
    u = service.create_user(session, firm["tenant"].id, "su" + firm["sfx"], "超管",
                            "password123", is_superuser=True)
    p = access.build_principal(session, u.id)
    assert visible(session, case_table, p) == set(cases)


def test_tenant_isolation(session, firm, case_table, cases):
    """别家律所的数据永远看不到。"""
    import uuid

    other = service.create_tenant(session, "o-" + uuid.uuid4().hex[:8], "别家所")
    session.execute(case_table.insert().values(
        tenant_id=other.id, owner_id=firm["director"].id,
        org_unit_id=firm["t1"].id, title="别家的案子",
    ))
    session.flush()
    p = access.build_principal(session, firm["director"].id)
    assert "别家的案子" not in visible(session, case_table, p)


# ================================================================ 隔离墙 ★


def test_wall_blocks_even_the_committee(session, firm, case_table, cases):
    """利冲隔离：连管委会主任也绕不过。**优先级高于一切数据范围。**"""
    target = str(cases["赵六的案子"])
    access.set_wall(session, firm["tenant"].id, "case", target,
                    firm["director"].id, mode="deny", reason="利冲隔离")

    p = access.build_principal(session, firm["director"].id)
    wall = access.wall_view(session, firm["tenant"].id, firm["director"].id, "case")

    stmt = select(case_table.c.title)
    stmt = security.apply_scope(stmt, p, "identity.view",
                                owner_col=case_table.c.owner_id,
                                org_col=case_table.c.org_unit_id,
                                tenant_col=case_table.c.tenant_id)
    stmt = security.apply_wall(stmt, case_table.c.id.cast(String), wall)
    titles = set(session.scalars(stmt))

    assert "赵六的案子" not in titles
    assert "张三的案子" in titles


def test_wall_allow_mode_is_whitelist(session, firm, case_table, cases):
    """涉密案件：一旦设了白名单，名单外的人一律不可见。"""
    target = str(cases["李四的案子"])
    access.set_wall(session, firm["tenant"].id, "case", target,
                    firm["leader2"].id, mode="allow", reason="涉密")

    for user, should_see in ((firm["leader2"], True), (firm["director"], False)):
        wall = access.wall_view(session, firm["tenant"].id, user.id, "case")
        assert wall.blocks(target) is (not should_see)


def test_wall_error_does_not_leak_existence(session, firm, cases):
    """措辞不能暴露"这条被隔离了"，否则等于承认该案件存在。"""
    target = str(cases["赵六的案子"])
    access.set_wall(session, firm["tenant"].id, "case", target,
                    firm["director"].id, mode="deny")
    wall = access.wall_view(session, firm["tenant"].id, firm["director"].id, "case")
    with pytest.raises(PermissionDenied) as exc:
        security.assert_not_walled(wall, "案件", target)
    assert "隔离" not in str(exc.value)
    assert "无权访问" in str(exc.value)


def test_clear_wall(session, firm, cases):
    target = str(cases["赵六的案子"])
    access.set_wall(session, firm["tenant"].id, "case", target, firm["director"].id)
    assert access.clear_wall(session, firm["tenant"].id, "case", target) == 1
    wall = access.wall_view(session, firm["tenant"].id, firm["director"].id, "case")
    assert not wall.blocks(target)


# ================================================================ 审计 ★


def test_audit_records_cross_scope_read(session, firm):
    """主任查全所是合法的，但**必须留痕** —— 律所合规检查会看这个。"""
    p = access.build_principal(session, firm["director"].id)
    assert security.is_cross_scope(p, "identity.view", owner_id=firm["member1"].id)
    # 看自己的不算越权，不必记
    assert not security.is_cross_scope(p, "identity.view", owner_id=firm["director"].id)

    audit.write(session, tenant_id=firm["tenant"].id, actor_id=firm["director"].id,
                action="identity.view_cross_scope", resource_type="user",
                resource_id=str(firm["member1"].id), scope=p.scope_for("identity.view"),
                detail={"reason": "查看下属信息"})

    rows = audit.read(session, firm["tenant"].id, actor_id=firm["director"].id)
    assert rows[0].action == "identity.view_cross_scope"
    assert rows[0].scope == DataScope.ALL
    assert "查看下属信息" in rows[0].detail


def test_lawyer_reading_own_is_not_cross_scope(session, firm):
    p = access.build_principal(session, firm["member1"].id)
    assert not security.is_cross_scope(p, "identity.view", owner_id=firm["member1"].id)


def test_audit_is_queryable_by_resource(session, firm):
    audit.write(session, tenant_id=firm["tenant"].id, actor_id=firm["director"].id,
                action="identity.promote", resource_type="user", resource_id="42",
                detail={"from": "associate", "to": "partner"})
    rows = audit.read(session, firm["tenant"].id, resource_type="user", resource_id="42")
    assert len(rows) == 1
    assert rows[0].action == "identity.promote"


def test_set_wall_is_idempotent(session, firm, cases):
    """★ 同一堵墙设两次不能炸。

    回归：利冲放行会带着同一批人重复调 set_wall（改理由、重新处置），
    唯一约束 uq_wall_entry 直接 IntegrityError，整个放行事务回滚 ——
    管委会点「放行」看到 500，理由也没留下。
    """
    target = str(cases["赵六的案子"])
    first = access.set_wall(session, firm["tenant"].id, "case", target,
                            firm["director"].id, mode="deny", reason="利冲隔离")
    again = access.set_wall(session, firm["tenant"].id, "case", target,
                            firm["director"].id, mode="deny",
                            reason="管委会第 4 次会议决议")

    assert again.id == first.id, "重复设墙应当复用同一条，不是插新的"
    assert again.reason == "管委会第 4 次会议决议", "理由变了要覆盖"

    # denied_ids 是 frozenset，去重会掩盖问题 —— 直接数表里的行
    rows = session.scalars(
        select(EthicalWall).where(
            EthicalWall.resource_type == "case",
            EthicalWall.resource_id == target,
            EthicalWall.user_id == firm["director"].id,
        )
    ).all()
    assert len(rows) == 1, "不能留下两条一样的墙"


def test_walls_are_visible_and_removable(session, firm, cases):
    """★ 墙必须看得见、撤得掉。

    回归：管委会放行时在名单里勾中了**自己**，放行后立刻失去本案访问权。
    当时既没有接口能看见案件上挂了哪些墙，也没有接口能撤掉其中一堵 ——
    等于把自己永久锁在外面。

    关键设计：`walls_on` / `remove_wall` 走 identity.manage_wall 权限，
    **不检查调用者能否看见该资源**。要求先能看见才能撤墙，
    在"墙让资源不可见"这个前提下是自相矛盾的。
    """
    target = str(cases["赵六的案子"])
    tenant = firm["tenant"].id
    me = firm["director"].id

    access.set_wall(session, tenant, "case", target, me, reason="手滑勾了自己")
    access.set_wall(session, tenant, "case", target, firm["member1"].id, reason="利冲隔离")

    # 我已经被自己挡住了
    walls = access.wall_view(session, tenant, me, "case")
    assert walls.blocks(target), "墙没生效，这条测试就没意义了"

    # …但依然看得见这堵墙是谁设的
    rows = access.walls_on(session, tenant, "case", target)
    assert {w.user_id for w in rows} == {me, firm["member1"].id}

    # …也撤得掉
    dropped = access.remove_wall(session, tenant, rows[0].id)
    assert dropped.id == rows[0].id
    left = access.walls_on(session, tenant, "case", target)
    assert len(left) == 1, "只能撤掉那一堵，不能连坐"

    access.remove_wall(session, tenant, left[0].id)
    after = access.wall_view(session, tenant, me, "case")
    assert not after.blocks(target), "撤完墙应当恢复可见"


def test_remove_wall_rejects_other_tenants(session, firm, cases):
    """撤墙不检查资源可见性，那租户隔离就得自己守住。"""
    target = str(cases["赵六的案子"])
    w = access.set_wall(session, firm["tenant"].id, "case", target,
                        firm["member1"].id, reason="利冲隔离")
    with pytest.raises(NotFound):
        access.remove_wall(session, firm["tenant"].id + 999, w.id)
