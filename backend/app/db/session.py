"""SQLAlchemy 引擎与会话工厂。

本项目接口是异步的，但 MySQL 驱动用同步 pymysql（不用 asyncmy，
避免 MySQL 5.5 兼容问题），所以数据库调用统一放在线程池里执行。
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(url: str) -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def new_session(url: str) -> Session:
    get_engine(url)
    assert _session_factory is not None
    return _session_factory()
