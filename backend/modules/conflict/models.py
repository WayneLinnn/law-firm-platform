"""利冲检查。全部落在 conflict schema。

律所的执业红线：不能同时代理利益冲突的双方。违反的后果是被投诉、被处分、
赔执业险 —— 所以这个模块的两条硬要求是：

  1. **必须留档** —— 检查是谁在什么时候做的、命中了什么、谁批准放行的，
     律协检查和事后追责都要看这份记录。删不得、改不得。

  2. **必须卡住流程** —— 命中未处置的案子不能进入办理中。这不能靠人记，
     要写进代码（见 case 模块对 conflict 的可选依赖）。

两张表：
    ConflictCheck  一次检查（一个案件可以查多次，每次留一条）
    ConflictHit    这次检查命中的每一条，逐条处置
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "conflict"


def _table_args(*args):
    return args + ({"schema": SCHEMA},)


# ---------------------------------------------------------------- 检查


#: 一次检查的结论
#:   pending  已跑出结果，等人处置（有 review 级命中）
#:   cleared  无冲突，或只有提示级 —— 可以继续
#:   waived   有冲突但经批准放行（必须写理由，通常同时设隔离墙）
#:   blocked  阻断，不能接这个案子
CHECK_STATUSES = ("pending", "cleared", "waived", "blocked")

#: 可以继续办案的结论
PASSING_STATUSES = ("cleared", "waived")


class ConflictCheck(Base):
    """一次利冲检查。**记录不可删改** —— 事后追责要看它。"""

    __tablename__ = "conflict_check"
    __table_args__ = _table_args(
        Index("ix_check_case", "case_id"),
        Index("ix_check_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: case.case.id
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)

    requested_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: 谁批的（放行或阻断）。管委会职权
    decided_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: 检查当时的当事人快照（JSON）。当事人后来改了，也要能还原当时查的是什么
    snapshot: Mapped[str] = mapped_column(Text, default="{}", nullable=False)


# ---------------------------------------------------------------- 命中


#: 严重程度
#:   blocked  阻断：对方就是我们在办案件的当事人 —— 不能接
#:   review   需审查：关联方冲突、所内另一团队在代理对方 —— 管委会决定
#:   notice   提示：已结案的旧关系 —— 记录即可
HIT_LEVELS = ("blocked", "review", "notice")

#: 冲突类型
HIT_KINDS = (
    "direct",       # 对方正是我们的客户/我方当事人
    "affiliate",    # 对方是我们客户的关联方（母子公司、法定代表人…）
    "firm_side",    # 所内另一团队正在代理对方 —— 需隔离墙
    "historical",   # 已结案的旧关系
)


class ConflictHit(Base):
    """一条命中。逐条处置，不能整体一键忽略。"""

    __tablename__ = "conflict_hit"
    __table_args__ = _table_args(
        Index("ix_hit_check", "check_id"),
        Index("ix_hit_level", "level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    check_id: Mapped[int] = mapped_column(
        ForeignKey("{0}.conflict_check.id".format(SCHEMA), ondelete="CASCADE"),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    #: 本案中触发命中的主体（crm.party.id）
    subject_party_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 与之冲突的既有主体（可能是关联方，不一定等于 subject）
    matched_party_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: 冲突来自哪个既有案件 / 客户
    related_case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    related_client_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    description: Mapped[str] = mapped_column(String(400), nullable=False)
    #: 处置结果：留空表示未处置
    resolution: Mapped[str] = mapped_column(String(400), default="", nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
