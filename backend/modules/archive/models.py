# -*- coding: utf-8 -*-
"""归档记录。落在 archive schema。

一张表：`archive_record` —— 一个案件归档那一刻的**完整快照清单**。

★ 为什么归档要单独一个模块，不塞进 case：

  归档要问遍所有模块「你这有几件」。而 doc / time / billing / approval
  **全都依赖 case** —— case 再去调它们就成环了。

  所以拆两半：
    · **拦写**留在 case（`assert_writable`）—— 大家本来就依赖它，无环
    · **编目录**归本模块 —— 只调别人，没人调它，也无环

★ 为什么要存清单而不是每次现算：

  归档的意义是「这一刻的卷是这样，以后能证明」。现算出来的数字会跟着
  数据变（哪怕只是有人补了一条咨询），那就证明不了任何事。
  **存下来，再存一个指纹 —— 将来能回答"这卷被动过吗"。**
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base

SCHEMA = "archive"


def _table_args(*extra):
    return tuple(extra) + ({"schema": SCHEMA},)


class ArchiveRecord(Base):
    """一次归档。**解档不删除这条记录**，只标 active=False —— 归档过就是归档过。"""

    __tablename__ = "archive_record"
    __table_args__ = _table_args(
        Index("ix_archive_case", "tenant_id", "case_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_id: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 卷号。全所唯一，写在卷皮上
    volume_no: Mapped[str] = mapped_column(String(64), nullable=False)

    #: 卷宗目录（JSON）：每个模块报的条目
    inventory: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    #: 总件数 —— 卷皮上"本卷共 X 件"那个数
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: ★ 目录指纹。将来能回答「这卷被动过吗」
    fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    archived_by: Mapped[int] = mapped_column(Integer, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: 保存到期日。律所自定年限，到期提醒（提醒本身还没做）
    keep_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    #: ---- 解档 ----
    #:
    #: ★ 归档不是终点。案子会再审、会申请执行，必须能解档。
    #:   没有解档，归档就是个死胡同，然后大家就都不敢归档了。
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unarchived_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    unarchived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 解档理由 —— 必填。解了档没人说得清为什么，等于没归过
    unarchive_reason: Mapped[str] = mapped_column(
        String(255), default="", nullable=False
    )
