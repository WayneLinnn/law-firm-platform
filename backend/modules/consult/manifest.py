"""法律咨询：按小时收费的那一种。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, Metric, ModuleManifest


class ClientParams(BaseModel):
    client_id: int = Field(description="客户 id")


MANIFEST = ModuleManifest(
    name="consult",
    version="0.1.0",
    description="法律咨询：一次咨询多久、收多少、能不能开票",
    #: ★ crm 是硬依赖 —— 咨询必须挂客户。
    #:   case **不是**依赖：多数咨询发生在立案之前，
    #:   把 case 写成硬依赖，就等于逼着"来聊一下没接案"这种情况必须先建个案子。
    requires=("identity", "crm"),
    #: case 可选：已经立案的咨询顺手关到案子上，没装也能记
    optional=("case",),
    permissions=(
        "consult.log",         # 记自己的咨询
        "consult.view",        # 看咨询
        "consult.view_all",    # 看别人的（团队负责人 / 管委会 / 财务）
        "consult.manage_rate", # 改本所咨询费率
    ),
    metrics=(
        Metric(key="consult.received", name="本期咨询费", unit="money",
               description="按小时收的咨询费。★ 不含办案的钱",
               scope_perm="consult.view", drill="/consults?f=period"),
    ),
    capabilities=(
        Capability(
            name="consult.by_client",
            description="一个客户咨询过几次、总共多少钱",
            params=ClientParams,
            requires_perm="consult.view",
            read_only=True,
        ),
    ),
)
