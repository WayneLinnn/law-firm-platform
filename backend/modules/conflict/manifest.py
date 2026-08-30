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
        # ★ **处置分两级** —— 看最高命中等级，见 service.required_perm。
        #   原来只有 decide 一档：所有命中都推给管委会。
        #   而管委会在中国律所不是天天在线的审批岗，是**开会的机构** ——
        #   把"关联方在别的案子出现过"也推给它，它待办里就堆满本该秒过的东西，
        #   然后养成"全点放行"的习惯，**这道闸就废了**。
        "conflict.review",  # 处置 review 级命中（风控 / 主任）
        "conflict.decide",  # 处置 blocked 级硬冲突（管委会）—— 是 review 的超集
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
