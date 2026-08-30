"""法律检索。真正的检索在独立服务里，本模块负责上下文注入与留档。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class SearchParams(BaseModel):
    query: str = Field(description="检索内容")
    case_id: int = Field(default=0, description="挂在哪个案件下，0 表示独立检索")
    top_k: int = Field(default=10, ge=1, le=50)


class HistoryParams(BaseModel):
    case_id: int = Field(description="案件 id")


MANIFEST = ModuleManifest(
    name="retrieval",
    version="0.1.0",
    description="法条与判例检索：注入案件上下文、结构化引用留档、花费记账",
    requires=("identity",),
    #: 有 case 就能注入案件上下文；没有也能做独立检索
    optional=("case", "crm"),
    permissions=(
        "retrieval.search",   # 纯检索（0 成本）
        "retrieval.ask",      # 问答/要素抽取/类案对比（有成本）
        "retrieval.view",     # 查检索历史
        "retrieval.cost",     # 查花费统计
    ),
    capabilities=(
        Capability(
            name="retrieval.search",
            description="检索法条与判例，返回带稳定标识的结构化引用",
            params=SearchParams,
            requires_perm="retrieval.search",
            read_only=True,
        ),
        Capability(
            name="retrieval.history",
            description="查某个案件下的检索历史",
            params=HistoryParams,
            requires_perm="retrieval.view",
            read_only=True,
        ),
    ),
)
