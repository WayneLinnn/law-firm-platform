"""业务逻辑。跨模块协作一律走 registry.api() / registry.optional_api()。"""
from __future__ import annotations

from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from kernel.errors import DomainError, NotFound
from modules.hello.models import HelloNote


def create_note(session: Session, title: str, body: str = "") -> HelloNote:
    if not title.strip():
        raise DomainError("标题不能为空", code="hello.title_required")
    note = HelloNote(title=title.strip(), body=body)
    session.add(note)
    session.flush()
    # EVENT: hello.note_created —— Step 待事件总线落地后替换为 publish()
    return note


def list_notes(session: Session, limit: int = 50) -> List[HelloNote]:
    stmt = select(HelloNote).order_by(HelloNote.id.desc()).limit(limit)
    return list(session.scalars(stmt))


def get_note(session: Session, note_id: int) -> HelloNote:
    note = session.get(HelloNote, note_id)
    if note is None:
        raise NotFound("笔记 {0} 不存在".format(note_id))
    return note
