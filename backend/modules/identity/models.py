"""组织与身份的表。全部落在 identity schema。

律所的真实结构（决定了下面的建模）：

    康桥律师事务所 (firm)
    ├── 管委会 (committee)      ← 权力机构，成员即"主任"，决定人员出入与晋升
    ├── 律师   (department)     ← 一个整体，不按业务细分部门
    │    ├── 王XX团队 (team)    ← 高级合伙人牵头
    │    ├── 李XX团队 (team)
    │    └── 陈XX团队 (team)
    ├── 财务部 (department)
    └── 行政部 (department)

**管理按职级，运作按团队。** 因此三个概念必须分开：

    职级 Position     你在所里是什么级别 —— 跟人走，在哪个团队都一样
    任职 Appointment  谁在什么时间段是什么职级 —— 晋升要留历史
    归属 Membership   你在哪个组织、担任什么角色 —— 跟组织走

举例：张三是「合伙人」（职级），在「王XX团队」担任「组员」（角色），
同时在「管委会」担任「主任」（角色）。职级只有一个，角色可以有多个。

三个后补代价极高的设计（都已落地）：
  1. Membership 独立成表 —— 律所是矩阵结构，一人可属多个团队
  2. Membership / Appointment 带有效期 —— 调岗与晋升都要留历史，
     否则历史报表全错（3 月调走，1 月的案子仍要算原团队）
  3. tenant_id 从第一天就带上 —— 将来多租户是加过滤条件，不是全表迁移
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "identity"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


def _fk(table: str) -> str:
    return "{0}.{1}.id".format(SCHEMA, table)


# ---------------------------------------------------------------- 租户（律所）


class Tenant(Base):
    __tablename__ = "tenant"
    __table_args__ = _table_args(UniqueConstraint("code", name="uq_tenant_code"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 组织树


#: 组织单元的种类。
#:   firm       所（树根）
#:   committee  管委会 —— 权力机构，与业务部门平行，不是它的下级
#:   department 部门 —— 只有三类：律师、财务、行政
#:   team       团队 —— 挂在"律师"下面，由高级合伙人牵头
ORG_KINDS = ("firm", "committee", "department", "team")


class OrgUnit(Base):
    __tablename__ = "org_unit"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "code", name="uq_org_unit_tenant_code"),
        Index("ix_org_unit_parent", "parent_id"),
        Index("ix_org_unit_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(_fk("org_unit"), ondelete="RESTRICT"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="department")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 职级字典


#: 人员大类。决定能不能计时、能不能承办案件。
POSITION_CATEGORIES = ("lawyer", "finance", "admin")


class Position(Base):
    """职级。律所可自行增删改 —— 这是**数据**不是代码。

    每个属性都驱动真实业务逻辑，所以不能用自由文本代替：
      is_partner      分配、表决
      can_bill        能否计时收费
      can_lead_team   能否牵头团队（律所规则：只有高级合伙人可以）
      can_handle_case 能否独立承办案件（律师助理不能）
      rank            排序与"高于某职级"判断，用于审批路由
    """

    __tablename__ = "position"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "code", name="uq_position_tenant_code"),
        Index("ix_position_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False, default="lawyer")
    #: 数值越大级别越高，用于比较与排序
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_partner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_bill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_lead_team: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_handle_case: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 任职（晋升历史）


class Appointment(Base):
    """谁在什么时间段是什么职级。

    晋升 **不是改字段**，而是关闭旧区间 + 开新区间 ——
    否则 6 月晋升合伙人后，5 月的案件分配会按新职级重算，账就错了。

    由管委会决定（权限点 identity.promote，Step 4 落地）。
    """

    __tablename__ = "appointment"
    __table_args__ = _table_args(
        Index("ix_appointment_user", "user_id"),
        Index("ix_appointment_span", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("user"), ondelete="CASCADE"), nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("position"), ondelete="RESTRICT"), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: 晋升决议文号等
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 用户


class User(Base):
    __tablename__ = "user"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),
        Index("ix_user_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 建所初期的超级管理员。Step 4 权限体系落地后应改为普通角色
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ------------------------------------------------------------ 执业资格
    #
    # ★ 这一组是**律所独有**的东西，别的公司系统里没有：
    #   一个人能不能办案，不取决于所里给他什么角色，
    #   取决于**司法行政机关发没发证、证还有没有效**。
    #
    # ★ 分三档而不是一个布尔（`practising` / `intern` / 空）：
    #   实习律师和执业律师在系统里的差别是实打实的 ——
    #   实习期不能独立承办、不能单独出庭。做成"是/否"就表达不了。
    #
    # ★ 注销、停止执业的人**不能删账号**（他办过的案子要留痕），
    #   所以用 `license_status` 标记，而不是把人停用。

    #: 执业证号。空 = 没登记（不等于没有）
    license_no: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: practising 执业中 / intern 实习 / suspended 停止执业 / revoked 注销 / "" 未登记
    license_status: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    #: 准予执业之日
    licensed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: 年检有效期至。★ 过期要在界面上说，但**不自动停人** —— 见 service.license_of
    license_valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: 执业机构（换所的人这里和本所不一致，是要查的）
    license_firm: Mapped[str] = mapped_column(String(200), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 归属（矩阵 + 时间区间）


#: 在组织里担任的角色。"主任"是管委会里的角色，不是职级。
LEADER_ROLE = "负责人"
DIRECTOR_ROLE = "主任"


class Membership(Base):
    """一个人在某段时间内属于某个组织，并在其中担任某个角色。

    valid_to 为 NULL 表示"至今仍在"。查任何历史数据都要带时间点。
    """

    __tablename__ = "membership"
    __table_args__ = _table_args(
        Index("ix_membership_user", "user_id"),
        Index("ix_membership_org", "org_unit_id"),
        Index("ix_membership_span", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("user"), ondelete="CASCADE"), nullable=False
    )
    org_unit_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("org_unit"), ondelete="CASCADE"), nullable=False
    )
    #: 在这个组织里担任什么 —— "负责人" / "主任" / "组员"。
    #: 与职级（Position）是两回事：职级跟人走，角色跟组织走。
    org_role: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    #: 主归属：用于统计口径（一个人同一时刻只能有一个）
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 角色与授权


class Role(Base):
    """角色 = 一组权限点 + 一个数据范围。**律所可自定义，这是数据不是代码。**

    业务代码永远不判断角色名，只判断权限点 —— 所以律所改角色配置不用改代码。
    """

    __tablename__ = "role"
    __table_args__ = _table_args(
        UniqueConstraint("tenant_id", "code", name="uq_role_tenant_code"),
        Index("ix_role_tenant", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: self | dept | dept_tree | custom | all，见 kernel.security.DataScope
    data_scope: Mapped[str] = mapped_column(String(16), default="self", nullable=False)
    description: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RolePermission(Base):
    """角色持有的权限点。权限点由各模块在 manifest 里注册，加模块自动多出可选项。"""

    __tablename__ = "role_permission"
    __table_args__ = _table_args(
        UniqueConstraint("role_id", "permission", name="uq_role_permission"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("role"), ondelete="CASCADE"), nullable=False
    )
    permission: Mapped[str] = mapped_column(String(64), nullable=False)


class RoleAssignment(Base):
    """谁在什么范围内拥有什么角色。

    org_unit_id 为空表示全所生效；填了则该角色只在这个组织内生效
    （对应 CUSTOM 数据范围）。同样带时间区间 —— 授权变更要留历史。
    """

    __tablename__ = "role_assignment"
    __table_args__ = _table_args(
        Index("ix_role_assignment_user", "user_id"),
        Index("ix_role_assignment_span", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("user"), ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("role"), ondelete="CASCADE"), nullable=False
    )
    org_unit_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(_fk("org_unit"), ondelete="CASCADE"), nullable=True
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- 隔离墙


#: deny  明确禁止某人访问（利冲隔离）
#: allow 该资源只有白名单内的人可见（涉密案件）
WALL_MODES = ("deny", "allow")


class EthicalWall(Base):
    """隔离墙。**优先级高于一切数据范围，主任也绕不过。**

    律所的执业红线：同一所内代理利益冲突双方时，两个团队之间必须信息隔离。
    """

    __tablename__ = "ethical_wall"
    __table_args__ = _table_args(
        UniqueConstraint(
            "resource_type", "resource_id", "user_id", "mode", name="uq_wall_entry"
        ),
        Index("ix_wall_resource", "tenant_id", "resource_type", "resource_id"),
        Index("ix_wall_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("tenant"), ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(_fk("user"), ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(8), default="deny", nullable=False)
    reason: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
