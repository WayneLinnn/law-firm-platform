from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel import audit, security
from kernel.db.session import get_session
from kernel.registry import registry
from kernel.security import Principal
from modules.crm import service
from modules.crm.models import Client, Party

router = APIRouter()

identity = registry.api("identity")
require_perm = identity.require_perm
client_ip = identity.client_ip


# ---------------------------------------------------------------- DTO


class PartyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    name: str
    id_no: Optional[str]
    aliases: str
    note: str


class PartyIn(BaseModel):
    name: str
    kind: str = "org"
    id_no: str = ""
    aliases: str = ""
    note: str = ""
    #: 确认过不是同一个主体时才置 true —— 默认拦住重复建档
    allow_similar: bool = False


class RelationIn(BaseModel):
    from_party_id: int
    to_party_id: int
    relation: str
    note: str = ""


class RelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_party_id: int
    to_party_id: int
    relation: str
    note: str


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    party_id: int
    code: str
    status: str
    source: str
    owner_id: int
    org_unit_id: Optional[int]
    onboarded_on: Optional[date]
    note: str


class ClientDetailOut(ClientOut):
    party: PartyOut
    contacts: List["ContactOut"]
    related_party_ids: List[int]


class ClientIn(BaseModel):
    party_id: int
    #: ★ 留空自动生成。**没人想自己编客户编号** —— 逼用户想一个，
    #:   结果就是一堆 "1"、"aaa"、"测试"，然后编号这一列彻底没用。
    code: str = ""
    owner_id: Optional[int] = Field(default=None, description="留空则记在自己名下")
    org_unit_id: Optional[int] = None
    status: str = "prospect"
    source: str = ""
    onboarded_on: Optional[date] = None
    note: str = ""


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    name: str
    title: str
    phone: str
    email: str
    is_primary: bool
    note: str


class ContactIn(BaseModel):
    name: str
    title: str = ""
    phone: str = ""
    email: str = ""
    is_primary: bool = False
    note: str = ""


class TransferIn(BaseModel):
    new_owner_id: int
    new_org_unit_id: Optional[int] = None


# ---------------------------------------------------------------- 主体


@router.get("/parties", response_model=List[PartyOut])
def search_parties(
    keyword: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_perm("crm.view")),
    session: Session = Depends(get_session),
) -> List[PartyOut]:
    """主体库是全所共享的，**不做数据范围过滤** —— 否则利冲会漏。

    但它只含名称与证件号，不含案情，泄露面很小。
    """
    rows = service.search_parties(session, principal.tenant_id, keyword, limit)
    return [PartyOut.model_validate(p) for p in rows]


@router.get("/parties/similar", response_model=List[PartyOut])
def similar_parties(
    name: str = Query(description="要建的主体名称"),
    principal: Principal = Depends(require_perm("crm.view")),
    session: Session = Depends(get_session),
) -> List[PartyOut]:
    """建档前的重名提示。前端在输入名称时调它。"""
    return [PartyOut.model_validate(p)
            for p in service.find_similar_parties(session, principal.tenant_id, name)]


@router.post("/parties", response_model=PartyOut)
def create_party(
    payload: PartyIn,
    request: Request,
    principal: Principal = Depends(require_perm("crm.manage_party")),
    session: Session = Depends(get_session),
) -> PartyOut:
    # ★ 角色给了按钮，职级还要再问一句够不够格 —— 律师助理挡在这里
    service.assert_can_onboard(session, principal.user_id)
    party = service.create_party(
        session, principal.tenant_id, payload.name, kind=payload.kind,
        id_no=payload.id_no, aliases=payload.aliases, note=payload.note,
        allow_similar=payload.allow_similar,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="crm.create_party", resource_type="party",
                resource_id=str(party.id), ip=client_ip(request),
                detail={"name": party.name, "id_no": party.id_no,
                        "allow_similar": payload.allow_similar})
    return PartyOut.model_validate(party)


@router.get("/parties/{party_id}/relations", response_model=List[RelationOut])
def party_relations(
    party_id: int,
    _: Principal = Depends(require_perm("crm.view")),
    session: Session = Depends(get_session),
) -> List[RelationOut]:
    return [RelationOut.model_validate(r) for r in service.relations_of(session, party_id)]


@router.post("/relations", response_model=RelationOut)
def relate(
    payload: RelationIn,
    request: Request,
    principal: Principal = Depends(require_perm("crm.manage_party")),
    session: Session = Depends(get_session),
) -> RelationOut:
    link = service.relate_parties(
        session, principal.tenant_id, payload.from_party_id,
        payload.to_party_id, payload.relation, payload.note,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="crm.relate_parties", resource_type="party",
                resource_id=str(payload.from_party_id), ip=client_ip(request),
                detail=payload.model_dump())
    return RelationOut.model_validate(link)


# ---------------------------------------------------------------- 客户


@router.get("/clients", response_model=List[ClientOut])
def list_clients(
    principal: Principal = Depends(require_perm("crm.view")),
    session: Session = Depends(get_session),
) -> List[ClientOut]:
    """走统一的数据范围过滤：律师只看自己的客户，团队负责人看整个团队的。"""
    stmt = select(Client)
    stmt = security.apply_scope(
        stmt, principal, "crm.view",
        owner_col=Client.owner_id, org_col=Client.org_unit_id,
        tenant_col=Client.tenant_id,
    )
    rows = session.scalars(stmt.order_by(Client.id.desc()))
    return [ClientOut.model_validate(c) for c in rows]


@router.get("/clients/{client_id}", response_model=ClientDetailOut)
def client_detail(
    client_id: int,
    principal: Principal = Depends(require_perm("crm.view")),
    session: Session = Depends(get_session),
) -> ClientDetailOut:
    client = service.get_client(session, client_id)
    _assert_visible(session, principal, client)
    party = service.get_party(session, client.party_id)
    return ClientDetailOut(
        **ClientOut.model_validate(client).model_dump(),
        party=PartyOut.model_validate(party),
        contacts=[ContactOut.model_validate(c)
                  for c in service.contacts_of(session, client_id)],
        related_party_ids=service.related_party_ids(
            session, client.party_id, include_self=False),
    )


@router.post("/clients", response_model=ClientOut)
def create_client(
    payload: ClientIn,
    request: Request,
    principal: Principal = Depends(require_perm("crm.manage")),
    session: Session = Depends(get_session),
) -> ClientOut:
    service.assert_can_onboard(session, principal.user_id)
    code = payload.code.strip() or service.next_client_code(
        session, principal.tenant_id)
    client = service.create_client(
        session, principal.tenant_id, payload.party_id, code,
        owner_id=payload.owner_id or principal.user_id,
        org_unit_id=payload.org_unit_id, status=payload.status,
        source=payload.source, onboarded_on=payload.onboarded_on, note=payload.note,
    )
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="crm.create_client", resource_type="client",
                resource_id=str(client.id), ip=client_ip(request),
                detail={"code": client.code, "party_id": client.party_id})
    return ClientOut.model_validate(client)


@router.post("/clients/{client_id}/transfer", response_model=ClientOut)
def transfer(
    client_id: int,
    payload: TransferIn,
    request: Request,
    principal: Principal = Depends(require_perm("crm.transfer")),
    session: Session = Depends(get_session),
) -> ClientOut:
    """客户移交（律师离所、团队调整）。改的是数据范围的归属，必须留痕。"""
    client = service.get_client(session, client_id)
    before = {"owner_id": client.owner_id, "org_unit_id": client.org_unit_id}
    service.transfer_client(session, client_id, payload.new_owner_id,
                            payload.new_org_unit_id)
    audit.write(session, tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="crm.transfer_client", resource_type="client",
                resource_id=str(client_id), ip=client_ip(request),
                detail={"before": before, "after": payload.model_dump()})
    return ClientOut.model_validate(client)


# ---------------------------------------------------------------- 联系人


@router.post("/clients/{client_id}/contacts", response_model=ContactOut)
def add_contact(
    client_id: int,
    payload: ContactIn,
    principal: Principal = Depends(require_perm("crm.manage")),
    session: Session = Depends(get_session),
) -> ContactOut:
    client = service.get_client(session, client_id)
    _assert_visible(session, principal, client)
    contact = service.add_contact(
        session, principal.tenant_id, client_id, payload.name,
        title=payload.title, phone=payload.phone, email=payload.email,
        is_primary=payload.is_primary, note=payload.note,
    )
    return ContactOut.model_validate(contact)


# ---------------------------------------------------------------- 内部


def _assert_visible(session: Session, principal: Principal, client: Client) -> None:
    """单条访问也要过数据范围 —— 列表过滤了但详情没过，是常见的越权入口。"""
    stmt = select(Client.id).where(Client.id == client.id)
    stmt = security.apply_scope(
        stmt, principal, "crm.view",
        owner_col=Client.owner_id, org_col=Client.org_unit_id,
        tenant_col=Client.tenant_id,
    )
    if session.scalar(stmt) is None:
        from kernel.errors import PermissionDenied

        raise PermissionDenied("无权访问该客户", code="crm.out_of_scope")


ClientDetailOut.model_rebuild()
