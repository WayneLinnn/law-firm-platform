"""客户与当事人。主体库是利冲检查（Step 7）的地基。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class SearchPartyParams(BaseModel):
    keyword: str = Field(description="名称、别名或证件号的片段")
    limit: int = Field(default=10, ge=1, le=50)


class ClientBriefParams(BaseModel):
    client_id: int = Field(description="客户 id")


MANIFEST = ModuleManifest(
    name="crm",
    version="0.1.0",
    description="主体库（自然人/法人）、主体关联、客户档案、联系人",
    requires=("identity",),
    optional=(),
    permissions=(
        "crm.view",            # 查客户与主体
        "crm.manage",          # 建改客户、联系人
        "crm.manage_party",    # 建改主体与关联关系（影响利冲，权限更重）
        "crm.transfer",        # 客户移交
    ),
    capabilities=(
        Capability(
            name="crm.searchParty",
            description="按名称或证件号查主体库，用于确认某个公司/个人是否已在库中",
            params=SearchPartyParams,
            requires_perm="crm.view",
            read_only=True,
        ),
        Capability(
            name="crm.clientBrief",
            description="查一个客户的基本情况：主体、负责人、联系人、关联方",
            params=ClientBriefParams,
            requires_perm="crm.view",
            read_only=True,
        ),
    ),
)
