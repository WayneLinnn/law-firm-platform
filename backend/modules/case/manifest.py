"""案件与事项。利冲检查（Step 7）与办案流程（Step 9-10）都挂在它上面。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, Metric, ModuleManifest


class SearchCaseParams(BaseModel):
    keyword: str = Field(default="", description="案件名称或编号的片段")
    status: str = Field(default="", description="限定状态，如 active")
    limit: int = Field(default=10, ge=1, le=50)


class CaseBriefParams(BaseModel):
    case_id: int = Field(description="案件 id")


MANIFEST = ModuleManifest(
    name="case",
    version="0.1.0",
    description="案由字典、案件、当事人、承办人、事项（程序阶段）",
    requires=("identity", "crm"),
    #: ★ 可选依赖：装了 conflict 就强制过利冲才能开案；
    #:   没装则降级放行（系统仍能跑，只是少了这道闸）。
    optional=("conflict",),
    permissions=(
        "case.view",          # 查案件
        "case.manage",        # 建改案件、登记当事人、指派承办人
        "case.close",         # 结案 / 终止（不可逆动作，单独授权）
        "case.manage_cause",  # 维护案由字典
    ),
    metrics=(
        # drill 只说"去案件表、看在办的"。**限定在谁的范围内是汇总层的事** ——
        # 同一个指标在工作台是"我经办的"、在全所页是"全所的"，
        # 模块自己不知道也不该知道现在是哪个范围。
        Metric(key="case.open", name="在办案件", unit="count",
               scope_perm="case.view", drill="/cases?f=open"),
        Metric(key="case.new", name="本期新增", unit="count",
               scope_perm="case.view", drill="/cases?f=new"),
        Metric(key="case.closed", name="本期结案", unit="count",
               scope_perm="case.view", drill="/cases?f=closed"),
        Metric(key="case.by_cause", name="案由分布", unit="count",
               scope_perm="case.view", drill="/cases?f=open"),
    ),
    capabilities=(
        Capability(
            name="case.search",
            description="按名称或编号查案件，可限定状态",
            params=SearchCaseParams,
            requires_perm="case.view",
            read_only=True,
        ),
        Capability(
            name="case.brief",
            description="查一个案件的概况：案由、当事人、承办人、事项、状态",
            params=CaseBriefParams,
            requires_perm="case.view",
            read_only=True,
        ),
    ),
)
