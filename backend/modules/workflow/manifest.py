"""流程编排。案件流程图 —— 与 RAG 的检索图是两张图，别混（见手册 4.6）。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class StartParams(BaseModel):
    template_key: str = Field(description="流程模板 key")
    case_id: int = Field(description="挂在哪个案件下")


class RunParams(BaseModel):
    run_id: str = Field(description="流程运行 id")


MANIFEST = ModuleManifest(
    name="workflow",
    version="0.1.0",
    description="案件流程编排：可中断、可恢复、跨进程续跑；人工确认只在这一层",
    #: 流程一定挂在案件下，所以 case 是硬依赖
    requires=("identity", "case"),
    #: 流程节点里可以调检索，但没有 retrieval 模块也能跑纯人工流程；
    #: 装了 doc 就把终审通过的意见书归档进案卷，没装照样跑完（只是不归档）
    #: 装了 task 就把律师确认过的要素落成案件上的长期时限
    optional=("retrieval", "doc", "task"),
    permissions=(
        "workflow.start",    # 发起流程
        "workflow.view",     # 看流程状态与流转记录
        "workflow.resume",   # 回答人工确认点（律师确认，责任在此）
        "workflow.cancel",   # 中止流程
    ),
    capabilities=(
        Capability(
            name="workflow.start",
            description="按模板为某个案件发起一条流程",
            params=StartParams,
            requires_perm="workflow.start",
        ),
        Capability(
            name="workflow.status",
            description="查一条流程当前跑到哪、在等什么",
            params=RunParams,
            requires_perm="workflow.view",
            read_only=True,
        ),
    ),
)
