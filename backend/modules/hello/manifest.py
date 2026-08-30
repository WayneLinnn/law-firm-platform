"""示例模块 —— 唯一用途是验证「可拆卸」。Step 5 起会被真实模块替换。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class ListNotesParams(BaseModel):
    """capability 的入参用 Pydantic 定义 —— 同时产出类型校验和 MCP inputSchema。"""

    limit: int = Field(default=10, ge=1, le=100, description="返回条数")


MANIFEST = ModuleManifest(
    name="hello",
    version="0.1.0",
    description="骨架自检模块：验证注册、路由挂载、独立 schema、独立迁移、能力目录",
    requires=(),
    optional=(),
    permissions=("hello.view", "hello.manage"),
    capabilities=(
        Capability(
            name="hello.listNotes",
            description="列出最近的笔记",
            params=ListNotesParams,
            requires_perm="hello.view",
            read_only=True,
        ),
    ),
)
