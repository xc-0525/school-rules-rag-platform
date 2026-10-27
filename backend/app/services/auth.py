"""登录鉴权（演示级实现）。

- 账号：MySQL `users` 表（sql/init_mysql.sql 种了 admin/admin123、student/student123）；
  未配置 MySQL（Mock 模式）时用内置的同款演示账号兜底。
- token：进程内随机 token（8 小时有效）；服务重启后需重新登录。
- 未上 JWT/Redis，原因是课程演示单进程即可；接口与前端已按 Bearer token 对接，
  后续换成 JWT 只需替换 issue/resolve 两个方法。
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from fastapi import Header, HTTPException

from app.core.config import Settings
from app.core.security import hash_password, verify_password

TOKEN_TTL_SECONDS = 8 * 60 * 60
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")

# Mock 模式兜底账号（与 sql/init_mysql.sql 的种子账号一致）
_DEMO_USERS: dict[str, tuple[str, str, int, str]] = {
    # username: (password, nickname, id, role)
    "admin": ("admin123", "管理员", 1, "admin"),
    "student": ("student123", "演示学生", 2, "student"),
}

# Mock 模式下注册的用户（仅进程内有效；真实 MySQL 模式会写 users 表）
_registered_users: dict[str, dict[str, object]] = {}
_registered_lock = threading.Lock()
_registered_seq = 1000


class RegistrationError(ValueError):
    """注册失败（用户名重复 / 格式不合法 / 未开放注册）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


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
    if demo is not None:
        demo_password, nickname, user_id, role = demo
        if not secrets.compare_digest(demo_password, password):
            return None
        return AuthUser(id=user_id, username=username, nickname=nickname, role=role)

    with _registered_lock:
        registered = dict(_registered_users.get(username) or {})
    if registered and verify_password(password, str(registered["password_hash"])):
        return AuthUser(
            id=int(registered["id"]),
            username=username,
            nickname=str(registered.get("nickname") or "") or None,
            role=str(registered.get("role") or "student"),
        )
    return None


def register_user(
    settings: Settings,
    username: str,
    password: str,
    nickname: str | None = None,
) -> AuthUser:
    """学生自助注册（默认 student 角色；管理员只预置，不开放注册）。"""
    username = (username or "").strip()
    password = password or ""
    nickname = (nickname or "").strip() or username

    if not settings.allow_registration:
        raise RegistrationError(403, "当前未开放注册")
    if not USERNAME_PATTERN.match(username):
        raise RegistrationError(400, "用户名需为 3-20 位字母、数字或下划线")
    if len(password) < 6:
        raise RegistrationError(400, "密码至少 6 位")
    if len(password) > 128:
        raise RegistrationError(400, "密码不能超过 128 位")
    if len(nickname) > 50:
        raise RegistrationError(400, "昵称不能超过 50 个字符")

    if settings.mysql_url:
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        from app.db.models import User
        from app.db.session import new_session

        try:
            with new_session(settings.mysql_url) as session:
                exists = session.execute(
                    select(User.id).where(User.username == username)
                ).scalar_one_or_none()
                if exists is not None:
                    raise RegistrationError(409, "用户名已被注册")
                user = User(
                    username=username,
                    password=hash_password(password),
                    nickname=nickname,
                    role="student",
                    status=1,
                    updated_at=datetime.now(),
                )
                session.add(user)
                session.commit()
                session.refresh(user)
                return AuthUser(
                    id=user.id,
                    username=user.username,
                    nickname=user.nickname,
                    role=str(user.role or "student"),
                )
        except RegistrationError:
            raise
        except IntegrityError as exc:  # 并发注册同名
            raise RegistrationError(409, "用户名已被注册") from exc

    global _registered_seq
    with _registered_lock:
        if username in _DEMO_USERS or username in _registered_users:
            raise RegistrationError(409, "用户名已被注册")
        _registered_seq += 1
        user_id = _registered_seq
        _registered_users[username] = {
            "id": user_id,
            "password_hash": hash_password(password),
            "nickname": nickname,
            "role": "student",
        }
    return AuthUser(id=user_id, username=username, nickname=nickname, role="student")


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
