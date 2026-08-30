"""文书生成。模板 + 案件数据填充 + 模型起草 + **引用校验**。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class CreateParams(BaseModel):
    case_id: int = Field(description="给哪个案件起草")
    template_key: str = Field(description="文书模板：civil_complaint / civil_defense / lawyer_letter / legal_opinion")


class DraftParams(BaseModel):
    draft_id: int = Field(description="文书 id")


MANIFEST = ModuleManifest(
    name="draft",
    version="0.1.0",
    description="文书生成：能填的直接填、要论述的才交给模型、引用逐条校验",
    #: 文书挂在案件下；依据来自检索
    requires=("identity", "case", "retrieval", "citation"),
    #: 装了 doc 才能定稿归档；装了 workflow 就能复用流程检索到的依据
    optional=("doc", "workflow", "crm"),
    permissions=(
        "draft.view",      # 看文书
        "draft.create",    # 建、生成、改
        "draft.finalize",  # 定稿归档（责任落点）
    ),
    capabilities=(
        Capability(
            name="draft.create",
            description="按模板为案件起草一份文书",
            params=CreateParams,
            requires_perm="draft.create",
        ),
        Capability(
            name="draft.assemble",
            description="取一份文书的完整正文",
            params=DraftParams,
            requires_perm="draft.view",
            read_only=True,
        ),
    ),
)
