"""引用留痕。依据快照 + 句子级锚点 + 溯源回检索。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class TraceParams(BaseModel):
    citation_id: int = Field(description="引用 id")


class DocParams(BaseModel):
    doc_kind: str = Field(default="draft", description="draft | workflow")
    doc_id: int = Field(description="文书 id")


MANIFEST = ModuleManifest(
    name="citation",
    version="0.1.0",
    description="引用留痕：点文书任一句看到出处、原文片段、时效状态、当时那次检索",
    #: 只依赖身份 —— 它不认识文书长什么样，用 (doc_kind, doc_id) 挂靠
    requires=("identity",),
    optional=(),
    permissions=(
        "citation.view",   # 看引用与溯源
    ),
    capabilities=(
        Capability(
            name="citation.trace",
            description="一条引用的完整来路：出处、原文、时效、当时那次检索",
            params=TraceParams,
            requires_perm="citation.view",
            read_only=True,
        ),
        Capability(
            name="citation.coverage",
            description="一份文书的引用覆盖情况",
            params=DocParams,
            requires_perm="citation.view",
            read_only=True,
        ),
    ),
)
