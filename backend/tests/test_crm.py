"""客户与当事人。

重点验证两件事 —— 它们决定 Step 7 的利冲检查能不能用：
  1. 主体去重：库里出现两条"甲公司"，利冲就形同虚设
  2. 关联方展开：只比名字查不出"对方是我们客户的全资子公司"
"""
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel import security
from kernel.errors import DomainError, PermissionDenied
from kernel.security import DataScope, Principal
from modules.crm import service
from modules.crm.models import Client
from modules.identity import access
from modules.identity import service as identity_service


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
    t = identity_service.create_tenant(session, "c-" + sfx, "康桥")
    identity_service.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)

    root = identity_service.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    law = identity_service.create_org_unit(session, t.id, "律师", "l-" + sfx, parent_id=root.id)
    t1 = identity_service.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                          kind="team", parent_id=law.id)
    t2 = identity_service.create_org_unit(session, t.id, "李团队", "t2-" + sfx,
                                          kind="team", parent_id=law.id)

    def person(name, position, org, role):
        u = identity_service.create_user(session, t.id, name + sfx, name, "password123")
        identity_service.appoint(session, u.id, position)
        identity_service.assign_membership(session, u.id, org.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    leader = person("wang", "senior_partner", t1, "team_leader")
    lawyer1 = person("zhang", "associate", t1, "lawyer")
    lawyer2 = person("li", "associate", t2, "lawyer")
    return dict(tenant=t, t1=t1, t2=t2, leader=leader,
                lawyer1=lawyer1, lawyer2=lawyer2, sfx=sfx)


# ================================================================ 主体去重 ★


def test_normalize_name_collapses_common_variants():
    """"甲公司 / 甲公司（北京） / 北京甲公司" 被当成三个主体是利冲失效的主因。"""
    n = service.normalize_name
    assert n("北京甲公司（分公司）") == n("北京甲公司分公司")
    assert n("甲有限公司") == n("甲")
    assert n(" 甲 · 公司 ") == n("甲公司")
    assert n("甲公司") != n("乙公司")


def test_same_id_no_is_rejected(session, firm):
    """有统一社会信用代码的，强制唯一。"""
    service.create_party(session, firm["tenant"].id, "甲公司", id_no="9111" + firm["sfx"])
    with pytest.raises(DomainError, match="已存在"):
        service.create_party(session, firm["tenant"].id, "甲公司（北京）",
                             id_no="9111" + firm["sfx"])


def test_similar_name_requires_confirmation(session, firm):
    """没有证件号时，同名主体默认拒绝建档 —— 强迫确认一次。"""
    service.create_party(session, firm["tenant"].id, "乙有限公司")
    with pytest.raises(DomainError, match="近似主体"):
        service.create_party(session, firm["tenant"].id, "乙公司")

    # 确认过确实是两个不同主体，才允许
    other = service.create_party(session, firm["tenant"].id, "乙公司", allow_similar=True)
    assert other.id


def test_alias_is_matched_too(session, firm):
    """曾用名也要参与比对，改名的公司不能变成第二条记录。"""
    service.create_party(session, firm["tenant"].id, "丙科技有限公司",
                         aliases="丙网络科技有限公司")
    hits = service.find_similar_parties(session, firm["tenant"].id, "丙网络科技")
    assert [p.name for p in hits] == ["丙科技有限公司"]


# ================================================================ 关联方 ★


def test_related_ids_are_bidirectional(session, firm):
    """关系是双向的：甲是乙的母公司，查乙时也要带出甲。"""
    a = service.create_party(session, firm["tenant"].id, "甲集团")
    b = service.create_party(session, firm["tenant"].id, "甲科技")
    service.relate_parties(session, firm["tenant"].id, a.id, b.id, "subsidiary")

    assert set(service.related_party_ids(session, b.id)) == {a.id, b.id}
    assert set(service.related_party_ids(session, a.id)) == {a.id, b.id}


def test_depth_limits_the_blast_radius(session, firm):
    """默认只展开一层。再深会把整个商业网络拉进来，利冲全是误报。"""
    a = service.create_party(session, firm["tenant"].id, "A集团")
    b = service.create_party(session, firm["tenant"].id, "B公司")
    c = service.create_party(session, firm["tenant"].id, "C公司")
    service.relate_parties(session, firm["tenant"].id, a.id, b.id, "subsidiary")
    service.relate_parties(session, firm["tenant"].id, b.id, c.id, "subsidiary")

    assert set(service.related_party_ids(session, a.id)) == {a.id, b.id}
    assert set(service.related_party_ids(session, a.id, depth=2)) == {a.id, b.id, c.id}


def test_conflict_check_building_block(session, firm):
    """★ Step 7 的利冲检查就是这两步。

    场景：我们代理「甲集团」；新案子的对方是「甲科技」；
    甲科技是甲集团的子公司 —— 单看名字查不出来，顺着关联方就能查出来。
    """
    ours = service.create_party(session, firm["tenant"].id, "甲集团")
    opponent = service.create_party(session, firm["tenant"].id, "甲科技")
    service.relate_parties(session, firm["tenant"].id, ours.id, opponent.id, "subsidiary")
    service.create_client(session, firm["tenant"].id, ours.id, "C001",
                          owner_id=firm["lawyer1"].id, org_unit_id=firm["t1"].id)

    # 只比名字：查不出
    assert service.client_by_party(session, firm["tenant"].id, opponent.id) is None

    # 顺着关联方：查出来了
    ids = service.related_party_ids(session, opponent.id)
    hits = service.clients_by_parties(session, firm["tenant"].id, ids)
    assert ours.id in hits
    assert hits[ours.id].code == "C001"


def test_self_relation_rejected(session, firm):
    a = service.create_party(session, firm["tenant"].id, "自关联公司")
    with pytest.raises(DomainError, match="自己关联"):
        service.relate_parties(session, firm["tenant"].id, a.id, a.id, "affiliate")


def test_bad_relation_type_rejected(session, firm):
    a = service.create_party(session, firm["tenant"].id, "X公司")
    b = service.create_party(session, firm["tenant"].id, "Y公司")
    with pytest.raises(DomainError, match="关系类型"):
        service.relate_parties(session, firm["tenant"].id, a.id, b.id, "朋友")


# ================================================================ 客户


def test_one_party_one_client(session, firm):
    """同一个主体不能建两个客户档案。"""
    p = service.create_party(session, firm["tenant"].id, "重复客户公司")
    service.create_client(session, firm["tenant"].id, p.id, "C100", owner_id=firm["lawyer1"].id)
    with pytest.raises(DomainError, match="已经是客户"):
        service.create_client(session, firm["tenant"].id, p.id, "C101",
                              owner_id=firm["lawyer1"].id)


def test_client_code_unique(session, firm):
    p1 = service.create_party(session, firm["tenant"].id, "编号公司甲")
    p2 = service.create_party(session, firm["tenant"].id, "编号公司乙")
    service.create_client(session, firm["tenant"].id, p1.id, "C200", owner_id=firm["lawyer1"].id)
    with pytest.raises(DomainError, match="客户编号"):
        service.create_client(session, firm["tenant"].id, p2.id, "C200",
                              owner_id=firm["lawyer1"].id)


def test_primary_contact_is_unique(session, firm):
    p = service.create_party(session, firm["tenant"].id, "联系人公司")
    c = service.create_client(session, firm["tenant"].id, p.id, "C300",
                              owner_id=firm["lawyer1"].id)
    service.add_contact(session, firm["tenant"].id, c.id, "张经理", is_primary=True)
    service.add_contact(session, firm["tenant"].id, c.id, "李总", is_primary=True)

    contacts = service.contacts_of(session, c.id)
    assert [x.is_primary for x in contacts].count(True) == 1
    assert contacts[0].name == "李总"


def test_transfer_changes_visibility(session, firm):
    """客户移交改的是数据范围归属 —— 移交后原律师就看不到了。"""
    p = service.create_party(session, firm["tenant"].id, "移交公司")
    c = service.create_client(session, firm["tenant"].id, p.id, "C400",
                              owner_id=firm["lawyer1"].id, org_unit_id=firm["t1"].id)

    def visible_to(user_id):
        principal = access.build_principal(session, user_id)
        # crm.view 尚未加入默认角色，这里手工造一个 SELF 范围的主体
        p2 = Principal(
            user_id=principal.user_id, tenant_id=principal.tenant_id,
            permissions=frozenset({"crm.view"}),
            scopes={"crm.view": DataScope.SELF},
            org_ids=principal.org_ids, org_tree_ids=principal.org_tree_ids,
        )
        stmt = security.apply_scope(
            select(Client.code), p2, "crm.view",
            owner_col=Client.owner_id, org_col=Client.org_unit_id,
            tenant_col=Client.tenant_id,
        )
        return set(session.scalars(stmt))

    assert "C400" in visible_to(firm["lawyer1"].id)
    assert "C400" not in visible_to(firm["lawyer2"].id)

    service.transfer_client(session, c.id, firm["lawyer2"].id, firm["t2"].id)
    assert "C400" not in visible_to(firm["lawyer1"].id)
    assert "C400" in visible_to(firm["lawyer2"].id)


# ================================================================ 数据范围


def test_scope_filters_client_list(session, firm):
    """律师只看自己的客户；团队负责人看整个团队的。"""
    for i, (owner, org) in enumerate([
        (firm["lawyer1"].id, firm["t1"].id),
        (firm["leader"].id, firm["t1"].id),
        (firm["lawyer2"].id, firm["t2"].id),
    ]):
        p = service.create_party(session, firm["tenant"].id, "范围公司%d" % i)
        service.create_client(session, firm["tenant"].id, p.id, "S%d" % i,
                              owner_id=owner, org_unit_id=org)

    def visible(user_id, scope):
        base = access.build_principal(session, user_id)
        principal = Principal(
            user_id=base.user_id, tenant_id=base.tenant_id,
            permissions=frozenset({"crm.view"}), scopes={"crm.view": scope},
            org_ids=base.org_ids, org_tree_ids=base.org_tree_ids,
        )
        stmt = security.apply_scope(
            select(Client.code), principal, "crm.view",
            owner_col=Client.owner_id, org_col=Client.org_unit_id,
            tenant_col=Client.tenant_id,
        )
        return set(session.scalars(stmt))

    assert visible(firm["lawyer1"].id, DataScope.SELF) == {"S0"}
    assert visible(firm["leader"].id, DataScope.DEPT_TREE) == {"S0", "S1"}
    assert visible(firm["lawyer2"].id, DataScope.SELF) == {"S2"}


def test_party_library_is_shared_across_scopes(session, firm):
    """主体库全所共享，**不做范围过滤** —— 过滤了利冲就会漏。

    但它只含名称与证件号，不含案情，泄露面很小。
    """
    service.create_party(session, firm["tenant"].id, "共享主体公司")
    found = service.search_parties(session, firm["tenant"].id, "共享主体")
    assert len(found) == 1


def test_suffix_stripping_is_iterative(session, firm):
    """★ 后缀要**反复剥到底**，只剥一层会漏掉重名。

    "宏远建设集团有限公司" 剥一次 → "宏远建设集团"
    "宏远建设集团"         剥一次 → "宏远建设"
    只剥一层则两者不等，重名检查失效 —— 这个 bug 真实发生过。
    """
    n = service.normalize_name
    assert n("宏远建设集团有限公司") == n("宏远建设集团")
    assert n("甲股份有限公司") == n("甲有限公司") == n("甲公司") == n("甲")
    assert n("乙事务所") == n("乙")
    assert n("甲") != n("乙")


def test_dedup_catches_suffix_variants(session, firm):
    """端到端：建了「X集团有限公司」后，再建「X集团」要被拦下。"""
    service.create_party(session, firm["tenant"].id, "宏远建设集团有限公司")
    with pytest.raises(DomainError, match="近似主体"):
        service.create_party(session, firm["tenant"].id, "宏远建设集团")
    with pytest.raises(DomainError, match="近似主体"):
        service.create_party(session, firm["tenant"].id, "宏远建设（集团）有限责任公司")
