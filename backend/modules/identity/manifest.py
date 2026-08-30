"""组织与身份。整个平台的地基模块 —— 几乎所有模块都会依赖它。"""
from pydantic import BaseModel, Field

from kernel.manifest import Capability, ModuleManifest


class ListMembersParams(BaseModel):
    org_unit_id: int = Field(description="组织单元 id")
    at: str = Field(default="", description="查某个时间点的成员，格式 YYYY-MM-DD，留空为今天")
    include_descendants: bool = Field(default=False, description="是否含下级组织")


class WhoIsParams(BaseModel):
    username: str = Field(description="用户名")


MANIFEST = ModuleManifest(
    name="identity",
    version="0.1.0",
    description="租户、组织树、用户、多归属（带有效期）、JWT 认证",
    requires=(),
    optional=(),
    permissions=(
        "identity.view",          # 看组织、人员、职级
        "identity.manage_org",    # 增删改组织
        "identity.manage_user",   # 增删改人员、调岗
        "identity.promote",       # 晋升定级（管委会职权）
        "identity.manage_role",   # 配角色与权限
        "identity.manage_wall",   # 设隔离墙（利冲隔离）
        "audit.view",             # 查审计日志
    ),
    capabilities=(
        Capability(
            name="identity.listMembers",
            description="列出某个部门/团队在指定时间点的成员",
            params=ListMembersParams,
            requires_perm="identity.view",
            read_only=True,
        ),
        Capability(
            name="identity.whoIs",
            description="查某个同事的基本信息与当前所属部门/团队",
            params=WhoIsParams,
            requires_perm="identity.view",
            read_only=True,
        ),
    ),
)
