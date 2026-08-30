"""本模块的表全部落在 hello schema。删模块 = DROP SCHEMA hello CASCADE。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from kernel.db.base import Base, schema_of


class HelloNote(Base):
    __tablename__ = "note"
    __table_args__ = schema_of("hello")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
