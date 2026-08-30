from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from kernel.db.session import get_session
from modules.hello import service

router = APIRouter()


class NoteIn(BaseModel):
    title: str
    body: str = ""


class NoteOut(BaseModel):
    id: int
    title: str
    body: str

    model_config = ConfigDict(from_attributes=True)


@router.get("/ping")
def ping() -> dict:
    """不碰数据库 —— 用于验证模块装载与路由挂载。"""
    return {"module": "hello", "pong": True}


@router.post("/notes", response_model=NoteOut)
def create(payload: NoteIn, session: Session = Depends(get_session)) -> NoteOut:
    note = service.create_note(session, payload.title, payload.body)
    return NoteOut.model_validate(note)


@router.get("/notes", response_model=List[NoteOut])
def index(session: Session = Depends(get_session)) -> List[NoteOut]:
    return [NoteOut.model_validate(n) for n in service.list_notes(session)]
