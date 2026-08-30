"""数据库连接。惰性建立 —— 没有数据库时应用照样能启动。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from kernel.config import settings

_engine: Optional[Engine] = None
_factory: Optional[sessionmaker] = None


def get_engine() -> Engine:
    global _engine, _factory
    if _engine is None:
        connect_args = {}
        if settings.database_url.startswith("postgresql"):
            # 数据库不可达时快速失败，避免拖住应用启动
            connect_args["connect_timeout"] = settings.db_connect_timeout
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
            connect_args=connect_args,
        )
        _factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _get_factory() -> sessionmaker:
    get_engine()
    assert _factory is not None
    return _factory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _get_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""
    with session_scope() as session:
        yield session


def reset_engine() -> None:
    """测试用：切换 database_url 后重置连接。"""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _factory = None
