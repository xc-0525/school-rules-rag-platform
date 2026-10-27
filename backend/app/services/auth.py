"""登录鉴权（演示级实现）。

- 账号：MySQL `users` 表（sql/init_mysql.sql 种了 admin/admin123、student/student123）；
  未配置 MySQL（Mock 模式）时用内置的同款演示账号兜底。
- token：进程内随机 token（8 小时有效）；服务重启后需重新登录。
- 未上 JWT/Redis，原因是课程演示单进程即可；接口与前端已按 Bearer token 对接，
  后续换成 JWT 只需替换 issue/resolve 两个方法。
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.core.config import Settings
from app.core.security import verify_password

TOKEN_TTL_SECONDS = 8 * 60 * 60

# Mock 模式兜底账号（与 sql/init_mysql.sql 的种子账号一致）
_DEMO_USERS: dict[str, tuple[str, str, int, str]] = {
    # username: (password, nickname, id, role)
    "admin": ("admin123", "管理员", 1, "admin"),
    "student": ("student123", "演示学生", 2, "student"),
}


@dataclass
class AuthUser:
    id: int
    username: str
    nickname: str | None
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthTokenStore:
    """进程内 token 表（token → 用户 + 过期时间）。"""

    def __init__(self) -> None:
        self._tokens: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()

    def issue(self, user: AuthUser) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + TOKEN_TTL_SECONDS
        with self._lock:
            self._purge_locked()
            self._tokens[token] = {"user": user, "expires_at": expires_at}
        return token, TOKEN_TTL_SECONDS

    def resolve(self, token: str | None) -> AuthUser | None:
        if not token:
            return None
        with self._lock:
            self._purge_locked()
            row = self._tokens.get(token)
            if row is None:
                return None
            if float(row["expires_at"]) < time.time():
                self._tokens.pop(token, None)
                return None
            return row["user"]  # type: ignore[return-value]

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.pop(token, None)

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [
            token
            for token, row in self._tokens.items()
            if float(row["expires_at"]) < now
        ]
        for token in expired:
            self._tokens.pop(token, None)


auth_tokens = AuthTokenStore()


def authenticate(settings: Settings, username: str, password: str) -> AuthUser | None:
    """校验账号密码；MySQL 未配置时使用内置演示账号。"""
    username = (username or "").strip()
    if not username or not password:
        return None

    if settings.mysql_url:
        from sqlalchemy import select

        from app.db.models import User
        from app.db.session import new_session

        with new_session(settings.mysql_url) as session:
            user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None or int(user.status or 0) != 1:
            return None
        if not verify_password(password, user.password or ""):
            return None
        return AuthUser(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            role=str(user.role or "student"),
        )

    demo = _DEMO_USERS.get(username)
    if demo is None:
        return None
    demo_password, nickname, user_id, role = demo
    if not secrets.compare_digest(demo_password, password):
        return None
    return AuthUser(id=user_id, username=username, nickname=nickname, role=role)


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    if not value.lower().startswith("bearer "):
        return None
    token = value[7:].strip()
    return token or None


def resolve_token(authorization: str | None) -> AuthUser | None:
    token = _extract_bearer(authorization)
    if token is None:
        return None
    return auth_tokens.resolve(token)


def require_user(authorization: str | None = Header(default=None, alias="Authorization")) -> AuthUser:
    """必须登录（用于 /auth/me 等）。"""
    user = resolve_token(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期，请重新登录")
    return user


def build_current_user_dependency(settings: Settings):
    """按 AUTH_REQUIRED 生成依赖：

    - AUTH_REQUIRED=1：必须带有效 Bearer token，否则 401；
    - 否则允许匿名（本地 mock/测试/curl 兜底），返回 None。
    """

    def current_user(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> AuthUser | None:
        token = _extract_bearer(authorization)
        user = auth_tokens.resolve(token) if token else None
        if token and user is None:
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
        if settings.auth_required and user is None:
            raise HTTPException(status_code=401, detail="请先登录")
        return user

    return current_user
