"""账单。算得对、收得回、不违规。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, Metric, ModuleManifest


class CaseParams(BaseModel):
    case_id: int = Field(description="案件 id")


class ContingencyParams(BaseModel):
    case_id: int = Field(description="案件 id")
    rate: float = Field(default=0.0, description="拟定的风险比例，如 0.2 表示 20%")


class EmptyParams(BaseModel):
    pass


MANIFEST = ModuleManifest(
    name="billing",
    version="0.1.0",
    description="账单：三种收费模式、风险代理合法性硬拦、账单生成、回款与账龄",
    #: case 是硬依赖 —— 账单挂在案件上，可见性要走它
    requires=("identity", "case"),
    #: consult 可选：没装也能开固定费和风险代理的单，只是开不了咨询费账单
    optional=("consult", "crm"),
    permissions=(
        "billing.view",         # 看账单
        "billing.manage",       # 签收费约定、编草稿账单
        "billing.issue",        # 开出账单、红冲
        "billing.payment",      # 登记回款
        "billing.manage_rate",  # 维护费率表
    ),
    metrics=(
        # ★ 三个口径必须分清 —— 律所说"创收"通常指**已回款**
        Metric(key="billing.billed", name="本期开票", unit="money",
               description="开了账单，不代表收到",
               scope_perm="billing.view", drill="/receivables?f=billed"),
        Metric(key="billing.received", name="本期回款", unit="money",
               description="★ 钱到账了 —— 这才是创收",
               scope_perm="billing.view", drill="/receivables?f=received"),
        Metric(key="billing.outstanding", name="未收", unit="money",
               description="开了还没收回来的",
               scope_perm="billing.view", drill="/receivables?f=outstanding"),
        Metric(key="billing.overdue", name="已逾期", unit="money",
               description="过了约定付款日还没收到的",
               scope_perm="billing.view", drill="/receivables?f=overdue"),
    ),
    capabilities=(
        Capability(
            name="billing.case_billing",
            description="一个案子的收费全貌：约定、已开、已收、未收",
            params=CaseParams,
            requires_perm="billing.view",
            read_only=True,
        ),
        Capability(
            name="billing.check_contingency",
            description="★ 这个案子能不能做风险代理、最高收多少 —— 法定，不是所里的规矩",
            params=ContingencyParams,
            requires_perm="billing.view",
            read_only=True,
        ),
        Capability(
            name="billing.receivables",
            description="应收账款与账龄 —— 律所真正的风险是开了单收不回来",
            params=EmptyParams,
            requires_perm="billing.view",
            read_only=True,
        ),
    ),
)
