"""SQLAlchemy 引擎与会话工厂。

本项目接口是异步的，但 MySQL 驱动用同步 pymysql（不用 asyncmy，
避免 MySQL 5.5 兼容问题），所以数据库调用统一放在线程池里执行。

P2-8 修复：引擎按 URL 缓存（此前是全局单例，第一次 create_engine 后
再传别的 URL 会被静默忽略，导致多库/测试隔离/热切换全部失效），
并给首次初始化加了锁，避免并发建多个 Engine。
"""

from __future__ import annotations

import threading

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker[Session]] = {}
_lock = threading.Lock()


def get_engine(url: str) -> Engine:
    engine = _engines.get(url)
    if engine is not None:
        return engine
    with _lock:
        # 双重检查：并发首次调用只建一个
        if url not in _engines:
            _engines[url] = create_engine(
                url,
                pool_pre_ping=True,
                pool_recycle=1800,
                future=True,
            )
            _factories[url] = sessionmaker(bind=_engines[url], expire_on_commit=False)
        return _engines[url]


def new_session(url: str) -> Session:
    get_engine(url)
    return _factories[url]()


def dispose_all() -> None:
    """应用退出 / 测试收尾时释放连接池。"""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _factories.clear()
