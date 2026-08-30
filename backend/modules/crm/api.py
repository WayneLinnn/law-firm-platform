"""crm 模块的对外唯一出口。

Step 7 的利冲检查会这样用：

    crm = registry.api("crm")
    ids = crm.related_party_ids(session, party_id)       # 展开关联方
    hits = crm.clients_by_parties(session, tenant_id, ids)  # 哪些已是我们的客户
"""
from modules.crm.service import (
    add_contact,
    client_by_party,
    clients_by_parties,
    contacts_of,
    create_client,
    create_party,
    find_similar_parties,
    get_client,
    get_party,
    normalize_name,
    related_party_ids,
    relate_parties,
    relations_of,
    search_parties,
    transfer_client,
)

__all__ = [
    # 主体库（利冲的基本单位）
    "create_party", "get_party", "search_parties",
    "find_similar_parties", "normalize_name",
    # 关联方（利冲要顺着查）
    "relate_parties", "related_party_ids", "relations_of",
    # 客户
    "create_client", "get_client", "client_by_party", "clients_by_parties",
    "transfer_client",
    # 联系人
    "add_contact", "contacts_of",
]
