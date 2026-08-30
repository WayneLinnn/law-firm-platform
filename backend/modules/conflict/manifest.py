"""利冲检查。执业红线 —— 命中未处置的案子不能往下走。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class CaseConflictParams(BaseModel):
    case_id: int = Field(description="案件 id")


MANIFEST = ModuleManifest(
    name="conflict",
    version="0.1.0",
    description="利冲检查：跨案件、跨客户、含关联方的双向比对；命中留档与审批",
    requires=("identity", "crm", "case"),
    optional=(),
    permissions=(
        "conflict.run",     # 发起检查
        "conflict.view",    # 查看检查结果
        "conflict.decide",  # 放行/阻断（管委会职权）
    ),
    capabilities=(
        Capability(
            name="conflict.status",
            description="查一个案件的利冲检查结论与命中明细",
            params=CaseConflictParams,
            requires_perm="conflict.view",
            read_only=True,
        ),
    ),
)
