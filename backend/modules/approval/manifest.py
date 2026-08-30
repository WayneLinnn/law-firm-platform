"""单据流转。★ 财务只看得到单据和金额，看不到案情。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, Metric, ModuleManifest


class EmptyParams(BaseModel):
    pass


class CaseParams(BaseModel):
    case_id: int = Field(description="案件 id")


MANIFEST = ModuleManifest(
    name="approval",
    version="0.1.0",
    description="单据流转：请款、报销、用印、付款；流转链是数据；★ 结构性隔离案情",
    requires=("identity", "case"),
    #: billing 可选：没装也能走单，只是付完款不会自动回写代垫
    #: crm 可选：装了才能拿当事人名字做泄漏检查
    optional=("billing", "crm", "doc"),
    #: ★ 本模块有等人动手的事，报进统一待办中心。
    #:   实现在 api.inbox_items() —— 待办跟人走，没法静态声明。
    has_inbox=True,
    permissions=(
        "approval.view",          # 看单据
        "approval.request",       # 提单
        "approval.approve_team",  # 团队负责人这一道
        "approval.approve_firm",  # 管委会这一道
        "approval.pay",           # 财务付款
        "approval.seal",          # 用印执行
        "approval.manage_flow",   # 配流转规则
    ),
    metrics=(
        Metric(key="approval.to_decide", name="待我批", unit="count",
               scope_perm="approval.view", drill="/tickets?f=to_decide"),
        Metric(key="approval.to_pay", name="待付款", unit="count",
               scope_perm="approval.view", drill="/tickets?f=to_pay"),
    ),
    capabilities=(
        Capability(
            name="approval.inbox",
            description="待我处理的单据 —— 按权限算，不按指派",
            params=EmptyParams,
            requires_perm="approval.view",
            read_only=True,
        ),
        Capability(
            name="approval.case_tickets",
            description="一个案子上的单据",
            params=CaseParams,
            requires_perm="approval.view",
            read_only=True,
        ),
    ),
)
