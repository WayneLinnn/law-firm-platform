"""看板。★ 只汇总，不碰别人的表；围绕创收。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class OverviewParams(BaseModel):
    scope: str = Field(default="mine", description="mine 我的 / team 团队 / firm 全所")
    org_id: int = Field(default=0, description="scope=team 时指定团队")


MANIFEST = ModuleManifest(
    name="analytics",
    version="0.1.0",
    description="看板：各模块自报指标，这里只汇总。围绕创收",
    requires=("identity",),
    #: ★ 全都是可选 —— 看板只**问**别人要数，问不到就标"查不到"，
    #:   不该因为某个模块没装就起不来
    optional=("case", "billing", "task", "approval", "consult",
              "doc", "draft", "conflict", "archive"),
    permissions=(
        "analytics.view",       # 看我自己的、看我团队的
        "analytics.view_firm",  # 看全所
    ),
    capabilities=(
        Capability(
            name="analytics.overview",
            description="一个视角的看板：在办、创收（开票/回款/未收）、风险",
            params=OverviewParams,
            requires_perm="analytics.view",
            read_only=True,
        ),
    ),
)
