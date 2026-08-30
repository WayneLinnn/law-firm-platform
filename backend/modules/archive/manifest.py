"""归档：编卷宗目录、存指纹、能解档。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class CaseParams(BaseModel):
    case_id: int = Field(description="案件 id")


MANIFEST = ModuleManifest(
    name="archive",
    version="0.1.0",
    description="归档：卷宗目录、归档前阻断检查、指纹校验、解档",
    requires=("identity", "case"),
    #: ★ 全都是可选 —— 归档只**问**别人要目录，问不到就跳过并留日志。
    #:   做成必需依赖的话，卸掉任何一个模块归档就起不来了。
    optional=("doc", "draft", "workflow", "conflict", "retrieval",
              "task", "consult", "billing", "approval"),
    permissions=(
        "archive.view",       # 看卷宗目录
        "archive.do",         # 归档
        "archive.unarchive",  # 解档
        "archive.force",      # 带阻断项强制归档
    ),
    capabilities=(
        Capability(
            name="archive.inventory",
            description="卷宗目录：这个案子的卷里有什么，共几件",
            params=CaseParams,
            requires_perm="archive.view",
            read_only=True,
        ),
        Capability(
            name="archive.blockers",
            description="归档前的阻断检查：还有什么没了结",
            params=CaseParams,
            requires_perm="archive.view",
            read_only=True,
        ),
    ),
)
