# -*- coding: utf-8 -*-
"""法律咨询。全部落在 consult schema。

★ **这个模块是 time（工时）拆掉之后剩下的那一点点。**

  原来的 time 模块做的是英美计时制律所那一套：每个人每天记满 8 小时，
  按计费率换算成钱，团队负责人审批，月底出账。
  1752 行，三张表，还带漏记提醒和取整口径。

  中国的提成制律所不这么运转 —— 团队成员和律师助理是**工资制或分成制**，
  他们的时间不换算成客户账单。硬套一套工时制度下去，
  结果是所有人应付式地补录，数据全是假的，
  **而假数据比没数据更糟：它会被拿去算成本、算人效、算分成。**

  但有一件事确实按时间收钱：**法律咨询**。
  来聊一小时，收 1500 —— 这个要记、要能开票。
  所以留下的只有这一张表。

★ 一条咨询挂**客户**，案件**选填**。
  多数咨询发生在立案之前（甚至根本不会立案），
  只能挂案件的话，"来聊一下、收 1500、没接案"这种最典型的情况记不进去。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "consult"


def _table_args(*extra):
    return tuple(extra) + ({"schema": SCHEMA},)


#: 咨询方式。做成固定词表 —— 自由文本填出来的是「咨询」「沟通」，
#: 汇总时什么也看不出来。
CHANNELS = ("office", "phone", "online", "visit")

CHANNEL_LABELS = {
    "office": "来所面谈", "phone": "电话", "online": "线上", "visit": "外出",
}

#: 咨询状态。
#:
#: ★ 出账之后**不许改** —— 已经报给客户的数字事后被悄悄改掉，
#:   是律所最经不起查的一类问题。要改只能作废重记。
STATUSES = ("draft", "billed", "void")

LOCKED_STATUSES = ("billed", "void")


class Consult(Base):
    """一次法律咨询。

    ★ 金额**存下来，不每次重算**。
      费率会调 —— 今天调了价，去年那条咨询不该跟着变。
      算过一次就把结果钉死，这是记账的基本要求。
    """

    __tablename__ = "consult"
    __table_args__ = _table_args(
        Index("ix_consult_tenant_client", "tenant_id", "client_id"),
        Index("ix_consult_tenant_date", "tenant_id", "happened_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 谁做的咨询
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)

    #: ★ 客户**必填** —— 收了钱就得知道是谁的钱，
    #:   而且以后这人成了当事人，利冲要查得到他。
    client_id: Mapped[int] = mapped_column(Integer, nullable=False)

    #: ★ 案件**选填** —— 多数咨询发生在立案之前，甚至根本不会立案。
    case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    happened_on: Mapped[date] = mapped_column(Date, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    channel: Mapped[str] = mapped_column(String(12), default="office", nullable=False)

    #: 聊了什么。★ 这是客户砍账单时唯一能拿出来的东西
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: 每小时多少钱
    hourly_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )
    #: ★ 算好的钱**存下来**，不每次按当前费率重算
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0"), nullable=False
    )

    #: 免费咨询（第一次见面聊半小时不收钱，很常见）
    free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    free_reason: Mapped[str] = mapped_column(String(120), default="", nullable=False)

    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    #: 出到哪张账单上了
    invoice_no: Mapped[str] = mapped_column(String(40), default="", nullable=False)

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )
