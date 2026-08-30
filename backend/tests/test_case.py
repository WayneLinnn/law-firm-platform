"""案件与事项。

重点验证三件事：
  1. 状态机 —— 非法流转必须报错（已结案的案子不能再回到办理中）
  2. 当事人 —— 指向 crm 主体库，是 Step 7 利冲的另一半数据源
  3. 承办资格 —— 律师助理不能独立承办（Step 3 的职级开关驱动）
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel import security
from kernel.errors import DomainError, NotFound
from kernel.security import DataScope, Principal
from modules.case import service
from modules.case.models import Case
from modules.crm import service as crm
from modules.identity import access
from modules.identity import service as identity


@pytest.fixture(autouse=True)
def without_conflict():
    """本文件只测 case 自己的状态机，把 conflict 模块停掉。

    ★ 顺带验证了**可选依赖的降级路径**：conflict 没装时，
      案件照常能推进到「待立案」，系统不崩、只是少了利冲这道闸。
      利冲闸门本身在 test_conflict.py 里验证。
    """
    from kernel.config import settings
    from kernel.registry import registry

    original = settings.disabled_modules
    settings.disabled_modules = "conflict"
    registry.load(force=True)
    try:
        yield
    finally:
        settings.disabled_modules = original
        registry.load(force=True)


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
    t = identity.create_tenant(session, "k-" + sfx, "康桥")
    identity.ensure_default_positions(session, t.id)
    access.ensure_default_roles(session, t.id)
    causes = {c.code: c for c in service.ensure_default_causes(session, t.id)}

    root = identity.create_org_unit(session, t.id, "康桥", "r-" + sfx, kind="firm")
    law = identity.create_org_unit(session, t.id, "律师", "l-" + sfx, parent_id=root.id)
    t1 = identity.create_org_unit(session, t.id, "王团队", "t1-" + sfx,
                                  kind="team", parent_id=law.id)
    t2 = identity.create_org_unit(session, t.id, "李团队", "t2-" + sfx,
                                  kind="team", parent_id=law.id)

    def person(name, position, org, role):
        u = identity.create_user(session, t.id, name + sfx, name, "password123")
        identity.appoint(session, u.id, position)
        identity.assign_membership(session, u.id, org.id, is_primary=True)
        access.assign_role(session, u.id, role)
        return u

    leader = person("wang", "senior_partner", t1, "team_leader")
    lawyer = person("zhang", "associate", t1, "lawyer")
    other = person("li", "associate", t2, "lawyer")
    para = person("sun", "paralegal", t1, "lawyer")

    return dict(tenant=t, causes=causes, t1=t1, t2=t2, sfx=sfx,
                leader=leader, lawyer=lawyer, other=other, para=para)


def make_case(session, firm, code="A001", lead=None, org=None, **kw):
    return service.create_case(
        session, firm["tenant"].id, code, "测试案件 " + code,
        lead_id=(lead or firm["lawyer"]).id,
        cause_id=firm["causes"]["construction"].id,
        org_unit_id=(org or firm["t1"]).id, **kw,
    )


# ================================================================ 状态机


def test_allowed_transitions_are_declared_not_scattered():
    """★ 合法流转写死在一张表里，不是散在各处的 if。

    案件状态是全系统最容易长出隐性规则的地方 —— 一处一个 if，
    半年后没人说得清"已结案还能不能回到办理中"。
    """
    assert service.can_transition("draft", "conflict_check")
    assert not service.can_transition("draft", "active")
    assert not service.can_transition("closed", "active")


def test_archived_can_go_back_to_closed(session, firm):
    """★ 归档**不是终点**。案子会再审、会申请执行 —— 解档回到「已结案」。

    这里原来是空元组，于是解档在状态机上根本走不通：
    archive 模块写了 unarchive()，调 transition 必然失败。
    **一个走不通的解档，等于归档是死胡同，然后没人敢归档。**
    """
    assert service.can_transition("archived", "closed")


def test_terminated_is_final(session, firm):
    """已终止是真终态。"""
    assert service.ALLOWED_TRANSITIONS["terminated"] == ()


def test_illegal_transition_is_refused(session, firm):
    c = make_case(session, firm)
    with pytest.raises(DomainError) as e:
        service.transition(session, c.id, "closed", actor_id=firm["leader"].id)
    assert e.value.code == "case.illegal_transition"


def test_transition_is_logged(session, firm):
    """每一次流转都留痕 —— 谁、什么时候、从哪到哪。"""
    c = make_case(session, firm)
    service.transition(session, c.id, "conflict_check", actor_id=firm["leader"].id)
    from modules.case.models import CaseStatusLog

    logs = list(session.scalars(
        select(CaseStatusLog).where(CaseStatusLog.case_id == c.id)))
    assert logs and logs[-1].to_status == "conflict_check"


# ================================================================ 主办律师资格


def test_paralegal_cannot_lead_a_case(session, firm):
    """★ 律师助理不能独立承办 —— 规则来自职级开关，改一行数据就能放开。"""
    with pytest.raises(DomainError) as e:
        make_case(session, firm, code="P001", lead=firm["para"])
    assert e.value.code == "case.cannot_handle_case"
    # 报错要说清是哪个职级卡住的
    assert "律师助理" in e.value.message


def test_undefined_position_cannot_lead(session, firm):
    """没定级的人也不能当主办 —— 不是"默认可以"。"""
    u = identity.create_user(session, firm["tenant"].id,
                             "nopos" + firm["sfx"], "无级", "password123")
    with pytest.raises(DomainError) as e:
        make_case(session, firm, code="N001", lead=u)
    assert e.value.code == "case.no_position"


# ================================================================ 当事人


def test_parties_point_at_the_shared_party_library(session, firm):
    """★ 当事人指向**全所主体库** —— 利冲检查靠它跨案件比对。

    每个案子自己存一份当事人名字，利冲就永远查不出来。
    """
    c = make_case(session, firm, code="B001")
    p = crm.create_party(session, firm["tenant"].id, "某某公司", "org")
    service.add_party(session, firm["tenant"].id, c.id, p.id, "defendant")
    assert service.parties_of(session, c.id)[0].party_id == p.id


def test_party_role_must_be_known(session, firm):
    c = make_case(session, firm, code="B002")
    p = crm.create_party(session, firm["tenant"].id, "某某公司2", "org")
    with pytest.raises(DomainError) as e:
        service.add_party(session, firm["tenant"].id, c.id, p.id, "路人甲")
    assert e.value.code == "case.bad_party_role"


# ================================================================ 数据范围


def test_lawyer_sees_only_their_own_cases(session, firm):
    """SELF 范围：只看得到自己主办的。"""
    make_case(session, firm, code="S001", lead=firm["lawyer"])
    make_case(session, firm, code="S002", lead=firm["leader"])

    p = access.build_principal(session, firm["lawyer"].id)
    rows = list(session.scalars(service.scoped_cases(session, p)))
    assert {c.code for c in rows} == {"S001"}


def test_team_leader_sees_the_team(session, firm):
    """团队负责人看得到本团队的 —— 包括别人主办的。"""
    make_case(session, firm, code="T001", lead=firm["lawyer"])
    p = access.build_principal(session, firm["leader"].id)
    rows = list(session.scalars(service.scoped_cases(session, p)))
    assert "T001" in {c.code for c in rows}


def test_another_team_is_out_of_scope(session, firm):
    """别的团队的案子看不到。"""
    make_case(session, firm, code="X001", lead=firm["lawyer"], org=firm["t1"])
    p = access.build_principal(session, firm["other"].id)
    rows = list(session.scalars(service.scoped_cases(session, p)))
    assert "X001" not in {c.code for c in rows}


def test_assert_visible_says_out_of_scope(session, firm):
    """单条访问也要过同一道 —— 列表和详情不能两套规则。"""
    from kernel.errors import PermissionDenied

    c = make_case(session, firm, code="V001", lead=firm["lawyer"], org=firm["t1"])
    p = access.build_principal(session, firm["other"].id)
    with pytest.raises(PermissionDenied) as e:
        service.assert_visible(session, p, c.id)
    assert e.value.code == "case.out_of_scope"


# ================================================================ 归档锁


def test_archived_case_is_read_only(session, firm):
    """★ 归档 = 拦住写入。别的模块要判断"这案子还能不能写"，
    从 case 这里拿 —— 谁的数据谁定规则。"""
    c = make_case(session, firm, code="A100")
    c.status = "archived"
    session.flush()
    with pytest.raises(DomainError) as e:
        service.assert_writable(session,
                                access.build_principal(session, firm["leader"].id),
                                c.id)
    assert e.value.code == "case.frozen"
    # 报错要给出路
    assert "解档" in e.value.message


def test_terminated_case_is_read_only(session, firm):
    c = make_case(session, firm, code="A101")
    c.status = "terminated"
    session.flush()
    with pytest.raises(DomainError):
        service.assert_writable(session,
                                access.build_principal(session, firm["leader"].id),
                                c.id)


# ================================================================ 事项


def test_matter_kind_must_be_known(session, firm):
    c = make_case(session, firm, code="M001")
    with pytest.raises(DomainError) as e:
        service.create_matter(session, firm["tenant"].id, c.id, "随便", kind="乱写")
    assert e.value.code == "case.bad_matter_kind"


def test_matters_can_run_in_parallel(session, firm):
    """★ 程序阶段可以并行（一审在跑、保全也在跑），所以事项独立成表。"""
    c = make_case(session, firm, code="M002")
    service.create_matter(session, firm["tenant"].id, c.id, "一审", kind="first_instance")
    service.create_matter(session, firm["tenant"].id, c.id, "保全", kind="preservation")
    assert len(service.matters_of(session, c.id)) == 2


# ================================================================ 案件编号


def test_case_code_is_unique_per_tenant(session, firm):
    make_case(session, firm, code="U001")
    with pytest.raises(DomainError) as e:
        make_case(session, firm, code="U001")
    assert e.value.code == "case.code_taken"


def test_conflict_module_absent_does_not_break_case(session, firm):
    """★ 可选依赖的降级路径：conflict 没装时案件照常能推进，
    系统不崩、只是少了利冲这道闸（本文件的 autouse fixture 停掉了 conflict）。"""
    c = make_case(session, firm, code="D001")
    service.transition(session, c.id, "conflict_check", actor_id=firm["leader"].id)
    service.transition(session, c.id, "pending", actor_id=firm["leader"].id)
    assert service.get_case(session, c.id).status == "pending"



# ================================================================ 案由字典


def test_civil_causes_are_the_supreme_court_text_verbatim(session, firm):
    """★★ 民事案由是**最高法法〔2020〕347 号的原文**，一条不少、一字不改。

    这条测试是**踩过坑之后加的**：第一版案由表是凭记忆写的 ——
    408 条，看着挺像那么回事，实际三级案由只有 299（原文 473），
    漏掉「声音保护纠纷」「碳排放权交易纠纷」「居住权合同纠纷」等
    民法典新增的，还把 137「服务合同纠纷」下的 22 个**四级**
    当成了平级的三级案由。

    **挂错层级案由编号就是错的；名字差一个字，立案就对不上。**
    案由要写进起诉状、要和法院案号对齐 —— 这种东西不能靠记忆。
    """
    import re

    from modules.case.causes_civil import CIVIL_CAUSES
    from modules.case.causes_civil2 import CIVIL_CAUSES_2

    lvl3 = {}
    for code, name, _parent, _tpl in CIVIL_CAUSES + CIVIL_CAUSES_2:
        m = re.fullmatch(r"c\d\d-\d\d-(\d+)", code)
        if m:
            lvl3[int(m.group(1))] = name

    # ★ 1–473 连号，一个不缺
    assert len(lvl3) == 473
    assert sorted(lvl3) == list(range(1, 474))

    # 抽查几条容易记错的
    assert lvl3[5] == "声音保护纠纷"            # 民法典新增
    assert lvl3[100] == "碳排放权交易纠纷"       # 绿色原则新增
    assert lvl3[136] == "居住权合同纠纷"         # 民法典新增
    assert lvl3[137] == "服务合同纠纷"           # 下面 22 个是四级不是三级
    assert lvl3[84] == "买卖合同纠纷"
    assert lvl3[115] == "建设工程合同纠纷"

    # 十一个部分一个不少
    codes = {c for c, *_ in CIVIL_CAUSES + CIVIL_CAUSES_2}
    for i in range(1, 12):
        assert "c{0:02d}".format(i) in codes

    # 五十四个二级案由一个不少
    lvl2 = [c for c in codes if re.fullmatch(r"c\d\d-\d\d", c)]
    assert len(lvl2) == 54, len(lvl2)


def test_service_contract_subtypes_are_level_four(session, firm):
    """★ 137「服务合同纠纷」下面那 22 个是**四级**，不是平级的三级案由。

    第一版把「法律服务合同纠纷」「医疗服务合同纠纷」当成了三级 ——
    挂错一层，案由编号就全错了。
    """
    from modules.case.causes_civil import CIVIL_CAUSES

    by_code = {c: (n, p) for c, n, p, _t in CIVIL_CAUSES}
    assert by_code["c04-10-137"][0] == "服务合同纠纷"
    # 法律服务是它的孩子
    assert by_code["c04-10-137-5"] == ("法律服务合同纠纷", "c04-10-137")


def test_unjust_enrichment_is_its_own_second_level(session, firm):
    """★ 不当得利、无因管理是**并列的两个二级案由**（十一、十二），
    不是"准合同纠纷"下面的子项 —— 第一版这里也错了。"""
    from modules.case.causes_civil import CIVIL_CAUSES

    by_code = {c: (n, p) for c, n, p, _t in CIVIL_CAUSES}
    assert by_code["c04-11"] == ("十一、不当得利纠纷", "c04")
    assert by_code["c04-12"] == ("十二、无因管理纠纷", "c04")


def test_workflow_template_points_at_a_real_template(session, firm):
    """★ 挂模板的案由，模板必须真的存在。

    钉住的是文档审计揪出的坑：案由字典里存着 `construction_v1` ——
    界面当权威标签显示，而真实 key 是 `construction.dispute`。
    **看起来在生效、其实没接线的字段，比空字段危险。**

    ★ 模板清单从**模板注册表**取，不问运行时容器 ——
    别的测试会往容器里绑桩引擎且不还原，那样这条会莫名其妙地红。
    """
    from modules.workflow.templates import TEMPLATES

    causes = service.ensure_default_causes(session, firm["tenant"].id)
    with_tpl = [c for c in causes if c.workflow_template]
    assert with_tpl, "至少建设工程该挂上模板"

    valid = {t.key for t in TEMPLATES}
    for c in with_tpl:
        assert c.workflow_template in valid, (c.code, c.workflow_template)


def test_seeding_is_idempotent(session, firm):
    """可重复调用 —— 每次启动都会跑一遍，不能每跑一次多一批。"""
    a = service.ensure_default_causes(session, firm["tenant"].id)
    b = service.ensure_default_causes(session, firm["tenant"].id)
    assert len(a) == len(b)
