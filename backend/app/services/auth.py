"""登录鉴权。

P0-1 / P0-2 改造要点：

1. **默认安全**：`Settings.auth_required` 默认 True；只有显式 `DEV_MODE=1` 才放匿名。
2. **匿名不再冒充管理员**：匿名请求使用独立的 `anonymous` 账号（MySQL 模式下自动
   seed 到 users 表，满足 documents.user_id 外键），不再回落 `user_id=1`。
3. **token 改为无状态签名令牌**（HMAC-SHA256，标准库实现，不引入新依赖）：
   配置 `AUTH_TOKEN_SECRET` 后多 worker / 重启均可用；未配置则退化为进程内随机密钥。
   登出通过吊销 jti 实现（吊销表在进程内，多 worker 下按实例生效，已在 README 说明）。

账号来源：MySQL `users` 表；未配置 MySQL（Mock 模式）时用环境变量
DEMO_ADMIN_PASSWORD / DEMO_STUDENT_PASSWORD 配置演示账号。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from fastapi import Header, HTTPException

from app.core.config import Settings
from app.core.security import hash_password, verify_password

logger = logging.getLogger("app.auth")

TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL", str(8 * 60 * 60)))
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")

# 保留用户名：管理员/学生演示账号不开放自助注册（密码不在仓库中出现）
_RESERVED_USERNAMES = {"admin", "student"}

# 匿名身份在 Mock 模式下的固定 id（MockPersistence 不做外键校验，取值只需稳定不冲突）
MOCK_ANONYMOUS_ID = 9001

# token 签名密钥：进程启动时确定一次，保证同一进程内多次建 app 的 token 互相有效。
# 配置了 AUTH_TOKEN_SECRET 时多 worker 共享；未配置时退化为进程内随机值（重启失效）。
_SECRET: bytes = (os.getenv("AUTH_TOKEN_SECRET", "").strip() or secrets.token_urlsafe(32)).encode(
    "utf-8"
)


def _demo_users() -> dict[str, tuple[str, str, int, str]]:
    """Mock 模式下的演示账号（密码从环境变量读取；未配置则不可用）。"""
    users: dict[str, tuple[str, str, int, str]] = {}
    admin_password = os.getenv("DEMO_ADMIN_PASSWORD", "").strip()
    student_password = os.getenv("DEMO_STUDENT_PASSWORD", "").strip()
    if admin_password:
        users["admin"] = (admin_password, "管理员", 1, "admin")
    if student_password:
        users["student"] = (student_password, "演示学生", 2, "student")
    return users


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


# ---------------------------------------------------------------------------
# 无状态签名 token（HMAC-SHA256 + base64url，标准库实现）
# ---------------------------------------------------------------------------
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(body: str, secret: bytes) -> str:
    return _b64e(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())


class AuthTokenStore:
    """签名令牌：`base64url(payload).base64url(hmac)`。

    - 校验不依赖进程内存（多 worker 可用，前提是配置 AUTH_TOKEN_SECRET）；
    - 登出通过吊销 jti 实现，吊销表在进程内（多 worker 下按实例生效）。
    """

    def __init__(self, secret: bytes | None = None, ttl: int = TOKEN_TTL_SECONDS) -> None:
        self._secret = secret or _SECRET
        self._ttl = ttl
        self._revoked: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self, user: AuthUser) -> tuple[str, int]:
        now = int(time.time())
        payload = {
            "sub": user.id,
            "u": user.username,
            "n": user.nickname or "",
            "r": user.role,
            "iat": now,
            "exp": now + self._ttl,
            "jti": secrets.token_hex(8),
        }
        body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        return f"{body}.{_sign(body, self._secret)}", self._ttl

    def resolve(self, token: str | None) -> AuthUser | None:
        if not token or "." not in token:
            return None
        body, _, signature = token.partition(".")
        if not body or not signature:
            return None
        # 先验签再解析内容，避免对不可信数据做 JSON 解析
        if not hmac.compare_digest(signature, _sign(body, self._secret)):
            return None
        try:
            payload = json.loads(_b64d(body))
        except (ValueError, TypeError):  # 非法 base64 / 非 JSON
            return None
        try:
            expires_at = float(payload["exp"])
            user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError):
            return None
        if expires_at < time.time():
            return None
        jti = str(payload.get("jti") or "")
        with self._lock:
            self._purge_locked()
            if jti and jti in self._revoked:
                return None
        return AuthUser(
            id=user_id,
            username=str(payload.get("u") or ""),
            nickname=str(payload.get("n") or "") or None,
            role=str(payload.get("r") or "student"),
        )

    def revoke(self, token: str | None) -> None:
        """登出：把 jti 加入吊销表（按 token 自身 exp 过期清理）。"""
        if not token or "." not in token:
            return
        body = token.partition(".")[0]
        try:
            payload = json.loads(_b64d(body))
        except (ValueError, TypeError):
            return
        jti = str(payload.get("jti") or "")
        if not jti:
            return
        with self._lock:
            self._revoked[jti] = float(payload.get("exp") or (time.time() + self._ttl))

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [jti for jti, exp in self._revoked.items() if exp < now]
        for jti in expired:
            self._revoked.pop(jti, None)


auth_tokens = AuthTokenStore()


# ---------------------------------------------------------------------------
# 匿名身份
# ---------------------------------------------------------------------------
_anonymous_lock = threading.Lock()
# 缓存键必须带上 mysql_url：否则测试/多实例场景下，先连 A 库拿到的 id
# 会被后续连 B 库的调用复用，导致匿名流量记到别的库/别的用户身上。
_anonymous_cache: dict[tuple[str, str], AuthUser] = {}


def anonymous_user(settings: Settings) -> AuthUser:
    """获取（必要时创建）匿名账号。

    绝不复用 user_id=1：那是 admin，会把匿名流量写进管理员会话，
    也会让 documents.user_id 归属错人。
    """
    username = settings.anonymous_username or "anonymous"
    cache_key = (settings.mysql_url or "", username)
    with _anonymous_lock:
        cached = _anonymous_cache.get(cache_key)
        if cached is not None:
            return cached

        resolved: AuthUser | None = None
        if settings.mysql_url:
            try:
                from sqlalchemy import select
                from sqlalchemy.exc import IntegrityError

                from app.db.models import User
                from app.db.session import new_session

                with new_session(settings.mysql_url) as session:
                    row = session.execute(
                        select(User).where(User.username == username)
                    ).scalar_one_or_none()
                    if row is None:
                        row = User(
                            username=username,
                            # 随机密码哈希：该账号无法用密码登录，只能由 dev_mode 以匿名身份使用
                            password=hash_password(secrets.token_urlsafe(24)),
                            nickname="匿名访客",
                            role="student",
                            status=1,
                            updated_at=datetime.now(),
                        )
                        session.add(row)
                        try:
                            session.commit()
                        except IntegrityError:  # 并发创建，回读已有记录
                            session.rollback()
                            row = session.execute(
                                select(User).where(User.username == username)
                            ).scalar_one()
                        else:
                            session.refresh(row)
                    resolved = AuthUser(
                        id=row.id,
                        username=row.username,
                        nickname=row.nickname,
                        role=str(row.role or "student"),
                    )
            except Exception:  # noqa: BLE001 —— 建号失败时降级为内存身份，不阻断问答
                logger.exception("匿名账号初始化失败，降级为内存身份")

        _anonymous_cache[cache_key] = resolved or AuthUser(
            id=MOCK_ANONYMOUS_ID, username=username, nickname="匿名访客", role="student"
        )
        return _anonymous_cache[cache_key]


# ---------------------------------------------------------------------------
# 账号校验 / 注册
# ---------------------------------------------------------------------------
def authenticate(settings: Settings, username: str, password: str) -> AuthUser | None:
    """校验账号密码；MySQL 未配置时使用内置演示账号。"""
    username = (username or "").strip()
    if not username or not password:
        return None
    # 匿名账号不允许用密码登录
    if username == (settings.anonymous_username or "anonymous"):
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

    demo = _demo_users().get(username)
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
    if username == (settings.anonymous_username or "anonymous"):
        raise RegistrationError(409, "该用户名不可用")

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
        if username in _RESERVED_USERNAMES or username in _registered_users:
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
    """按配置生成"当前用户"依赖。

    返回值**永远**是 AuthUser（不会返回 None）：
    - 带有效 token → 该用户；
    - 无 token 且 DEV_MODE=1 → 独立的 anonymous 账号；
    - 其它情况 → 401。
    """

    def current_user(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> AuthUser:
        token = _extract_bearer(authorization)
        user = auth_tokens.resolve(token) if token else None
        if token and user is None:
            raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
        if user is not None:
            return user
        if settings.dev_mode:
            return anonymous_user(settings)
        raise HTTPException(status_code=401, detail="请先登录")

    return current_user
