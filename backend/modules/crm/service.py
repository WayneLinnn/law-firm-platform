"""客户与当事人的业务逻辑。

两件事决定了这个模块的价值，其余都是 CRUD：

  1. **主体去重** —— 同一个"甲公司"在库里只能有一条。有证件号的强制唯一；
     没有证件号的，建之前先做名称近似匹配并提示。库里出现三条"甲公司"，
     利冲就形同虚设。

  2. **关联方展开** —— 利冲不能只比名字，要顺着母子公司、法定代表人、
     配偶这些边往外查一层。Step 7 的利冲检查直接用 related_party_ids()。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence, Set

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from kernel.registry import registry
from modules.crm.models import (
    CLIENT_STATUSES,
    PARTY_KINDS,
    PARTY_RELATIONS,
    Client,
    Contact,
    Party,
    PartyRelation,
)

# ---------------------------------------------------------------- 名称规整


#: 名称比对时忽略的噪声：括号、空白、标点
_NOISE = re.compile(r"[（）()\s·、,，.。\-—_]+")

#: 组织后缀。**按长度降序剥离，且要反复剥到不再变化**：
#:   "宏远建设集团有限公司" 只剥一次得到 "宏远建设集团"
#:   "宏远建设集团"         只剥一次得到 "宏远建设"
#: 两者不相等 —— 重名检查就漏了。必须循环剥到底。
_SUFFIXES = tuple(sorted(
    ("有限责任公司", "股份有限公司", "有限公司", "分公司", "办事处",
     "事务所", "集团", "公司"),
    key=len, reverse=True,
))


def normalize_name(name: str) -> str:
    """把名称压成可比对的形式。

    "北京甲公司（分公司）" 与 "北京甲公司分公司"、
    "宏远建设集团有限公司" 与 "宏远建设集团" 都会归一到同一个结果。

    这是"甲公司 / 甲公司（北京）/ 北京甲公司"被当成三个主体的主要来源，
    而主体一旦重复，利冲检查就形同虚设。宁可多提示一次。
    """
    text = _NOISE.sub("", (name or "").strip())
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text.lower()


# ---------------------------------------------------------------- 主体


def find_similar_parties(
    session: Session, tenant_id: int, name: str, limit: int = 5
) -> List[Party]:
    """建主体前的重名提示。宁可多提示一次，也不能让库里出现两条同一个主体。"""
    key = normalize_name(name)
    if not key:
        return []
    rows = session.scalars(select(Party).where(Party.tenant_id == tenant_id))
    hits = []
    for party in rows:
        candidates = [party.name] + [a for a in party.aliases.splitlines() if a.strip()]
        if any(normalize_name(c) == key for c in candidates):
            hits.append(party)
        if len(hits) >= limit:
            break
    return hits


def assert_can_onboard(session: Session, user_id: int) -> None:
    """能不能往库里加当事人/客户 —— **按职级判，不按角色判**。

    ★ 系统里有两条授权线，各管各的：
        **角色**管"界面上有没有这个按钮"（crm.manage_party）
        **职级**管"这个人够不够格干这事"（can_handle_case）

      合伙人、独立律师都在自己拉客户，所以按钮要给"律师"这个角色。
      但律师助理也是"律师"角色 —— 他不能自己收案，
      于是这里用职级把他挡住，和主办律师那道检查（case._require_can_handle）
      是同一个开关、同一句话。

    ★ 律所想放开就改一行数据（把助理的「可独立承办」打开），不用改代码。
    """
    identity = registry.api("identity")
    position = identity.position_of(session, user_id)
    if position is None:
        raise DomainError("你还没有定级，不能建客户 —— 找所务管理定级",
                          code="crm.no_position")
    if not position.can_handle_case:
        raise DomainError(
            "{0} 未开启「可独立承办」，不能自己建客户。\n"
            "让主办律师建，或请所务管理调整职级。".format(position.name),
            code="crm.cannot_onboard",
        )


def create_party(
    session: Session,
    tenant_id: int,
    name: str,
    kind: str = "org",
    id_no: str = "",
    aliases: str = "",
    note: str = "",
    allow_similar: bool = False,
) -> Party:
    """建主体。

    allow_similar=False 时，发现同名主体会拒绝并把已有的返回在错误里 ——
    强迫使用者确认一次，而不是默默建出第二条。
    """
    if kind not in PARTY_KINDS:
        raise DomainError("主体类型必须是 person 或 org", code="crm.bad_party_kind")
    if not name.strip():
        raise DomainError("主体名称不能为空", code="crm.party_name_required")

    code = (id_no or "").strip() or None
    if code:
        dup = session.scalar(
            select(Party).where(Party.tenant_id == tenant_id, Party.id_no == code)
        )
        if dup is not None:
            raise DomainError(
                "证件号 {0} 已存在：{1}".format(code, dup.name),
                code="crm.party_id_no_taken",
            )
    elif not allow_similar:
        similar = find_similar_parties(session, tenant_id, name)
        if similar:
            raise DomainError(
                "库里已有近似主体：{0}。确认不是同一个请显式允许".format(
                    "、".join(p.name for p in similar)
                ),
                code="crm.party_similar_exists",
            )

    party = Party(
        tenant_id=tenant_id, kind=kind, name=name.strip(), id_no=code,
        aliases=aliases.strip(), note=note.strip(),
    )
    session.add(party)
    session.flush()
    # EVENT: crm.party_created
    return party


def get_party(session: Session, party_id: int) -> Party:
    party = session.get(Party, party_id)
    if party is None:
        raise NotFound("主体 {0} 不存在".format(party_id))
    return party


def search_parties(
    session: Session, tenant_id: int, keyword: str = "", limit: int = 50
) -> List[Party]:
    stmt = select(Party).where(Party.tenant_id == tenant_id)
    if keyword.strip():
        like = "%{0}%".format(keyword.strip())
        stmt = stmt.where(or_(Party.name.ilike(like), Party.aliases.ilike(like),
                              Party.id_no.ilike(like)))
    return list(session.scalars(stmt.order_by(Party.id.desc()).limit(limit)))


# ---------------------------------------------------------------- 关联方


def relate_parties(
    session: Session,
    tenant_id: int,
    from_party_id: int,
    to_party_id: int,
    relation: str,
    note: str = "",
) -> PartyRelation:
    if relation not in PARTY_RELATIONS:
        raise DomainError(
            "关系类型必须是 {0} 之一".format("/".join(PARTY_RELATIONS)),
            code="crm.bad_relation",
        )
    if from_party_id == to_party_id:
        raise DomainError("主体不能与自己关联", code="crm.self_relation")
    get_party(session, from_party_id)
    get_party(session, to_party_id)

    dup = session.scalar(
        select(PartyRelation).where(
            PartyRelation.from_party_id == from_party_id,
            PartyRelation.to_party_id == to_party_id,
            PartyRelation.relation == relation,
        )
    )
    if dup is not None:
        return dup

    link = PartyRelation(
        tenant_id=tenant_id, from_party_id=from_party_id,
        to_party_id=to_party_id, relation=relation, note=note.strip(),
    )
    session.add(link)
    session.flush()
    return link


def related_party_ids(
    session: Session, party_id: int, depth: int = 1, include_self: bool = True
) -> List[int]:
    """展开关联方。**Step 7 的利冲检查直接用这个。**

    关系是双向的：甲是乙的母公司 ⇒ 查乙时也要带出甲。
    depth 默认 1 层 —— 再深会把整个商业网络拉进来，利冲会全是误报。
    """
    seen: Set[int] = {party_id}
    frontier: Set[int] = {party_id}
    for _ in range(max(0, depth)):
        if not frontier:
            break
        rows = session.scalars(
            select(PartyRelation).where(
                or_(PartyRelation.from_party_id.in_(frontier),
                    PartyRelation.to_party_id.in_(frontier))
            )
        )
        nxt: Set[int] = set()
        for row in rows:
            for pid in (row.from_party_id, row.to_party_id):
                if pid not in seen:
                    seen.add(pid)
                    nxt.add(pid)
        frontier = nxt
    if not include_self:
        seen.discard(party_id)
    return sorted(seen)


def relations_of(session: Session, party_id: int) -> List[PartyRelation]:
    return list(session.scalars(
        select(PartyRelation).where(
            or_(PartyRelation.from_party_id == party_id,
                PartyRelation.to_party_id == party_id)
        ).order_by(PartyRelation.id)
    ))


# ---------------------------------------------------------------- 客户


def next_client_code(session: Session, tenant_id: int,
                     today: Optional[date] = None) -> str:
    """自动生成客户编号：KH2026-0007。

    ★ 编号让用户自己填，填出来的就是 "1"、"aaa"、"测试" —— 这一列随即报废。
      能自动生成的东西不要问人。
    """
    today = today or date.today()
    prefix = "KH{0}-".format(today.year)
    n = session.scalar(
        select(func.count()).select_from(Client)
        .where(Client.tenant_id == tenant_id, Client.code.like(prefix + "%"))
    ) or 0
    while True:
        n += 1
        code = "{0}{1:04d}".format(prefix, n)
        hit = session.scalar(select(Client).where(
            Client.tenant_id == tenant_id, Client.code == code))
        if hit is None:
            return code


def create_client(
    session: Session,
    tenant_id: int,
    party_id: int,
    code: str,
    owner_id: int,
    org_unit_id: Optional[int] = None,
    status: str = "prospect",
    source: str = "",
    onboarded_on: Optional[date] = None,
    note: str = "",
) -> Client:
    if status not in CLIENT_STATUSES:
        raise DomainError(
            "客户状态必须是 {0} 之一".format("/".join(CLIENT_STATUSES)),
            code="crm.bad_client_status",
        )
    party = get_party(session, party_id)
    if party.tenant_id != tenant_id:
        raise DomainError("主体不属于本律所", code="crm.cross_tenant")

    exists = session.scalar(
        select(Client).where(Client.tenant_id == tenant_id, Client.party_id == party_id)
    )
    if exists is not None:
        raise DomainError(
            "{0} 已经是客户（编号 {1}）".format(party.name, exists.code),
            code="crm.client_exists",
        )
    dup_code = session.scalar(
        select(Client).where(Client.tenant_id == tenant_id, Client.code == code.strip())
    )
    if dup_code is not None:
        raise DomainError("客户编号 {0} 已存在".format(code), code="crm.client_code_taken")

    client = Client(
        tenant_id=tenant_id, party_id=party_id, code=code.strip(), status=status,
        source=source.strip(), owner_id=owner_id, org_unit_id=org_unit_id,
        onboarded_on=onboarded_on, note=note.strip(),
    )
    session.add(client)
    session.flush()
    # EVENT: crm.client_created
    return client


def get_client(session: Session, client_id: int) -> Client:
    client = session.get(Client, client_id)
    if client is None:
        raise NotFound("客户 {0} 不存在".format(client_id))
    return client


def client_by_party(session: Session, tenant_id: int, party_id: int) -> Optional[Client]:
    """这个主体是不是我们的客户 —— 利冲检查会问这个。"""
    return session.scalar(
        select(Client).where(Client.tenant_id == tenant_id, Client.party_id == party_id)
    )


def clients_by_parties(
    session: Session, tenant_id: int, party_ids: Sequence[int]
) -> Dict[int, Client]:
    """批量版本。利冲要一次问一串关联方，逐个查会很慢。"""
    if not party_ids:
        return {}
    rows = session.scalars(
        select(Client).where(
            Client.tenant_id == tenant_id, Client.party_id.in_(list(party_ids))
        )
    )
    return {c.party_id: c for c in rows}


def transfer_client(session: Session, client_id: int, new_owner_id: int,
                    new_org_unit_id: Optional[int] = None) -> Client:
    """客户移交（律师离所、团队调整）。"""
    client = get_client(session, client_id)
    client.owner_id = new_owner_id
    if new_org_unit_id is not None:
        client.org_unit_id = new_org_unit_id
    session.flush()
    # EVENT: crm.client_transferred
    return client


# ---------------------------------------------------------------- 联系人


def add_contact(
    session: Session,
    tenant_id: int,
    client_id: int,
    name: str,
    title: str = "",
    phone: str = "",
    email: str = "",
    is_primary: bool = False,
    note: str = "",
) -> Contact:
    get_client(session, client_id)
    if not name.strip():
        raise DomainError("联系人姓名不能为空", code="crm.contact_name_required")
    if is_primary:
        for c in contacts_of(session, client_id):
            c.is_primary = False
    contact = Contact(
        tenant_id=tenant_id, client_id=client_id, name=name.strip(), title=title.strip(),
        phone=phone.strip(), email=email.strip(), is_primary=is_primary, note=note.strip(),
    )
    session.add(contact)
    session.flush()
    return contact


def contacts_of(session: Session, client_id: int) -> List[Contact]:
    return list(session.scalars(
        select(Contact)
        .where(Contact.client_id == client_id)
        .order_by(Contact.is_primary.desc(), Contact.id)
    ))
