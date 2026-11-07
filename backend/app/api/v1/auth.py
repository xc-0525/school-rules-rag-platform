"""登录 / 当前用户 / 退出接口。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.config import Settings
from app.schemas.auth import LoginRequest, LoginResponse, LogoutResponse, RegisterRequest, UserInfo
from app.services.auth import (
    AuthUser,
    RegistrationError,
    auth_tokens,
    authenticate,
    build_current_user_dependency,
    register_user,
)

logger = logging.getLogger("app.api.auth")


def build_router(settings: Settings, limiter: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["鉴权"])

    @router.post(
        "/auth/login",
        response_model=LoginResponse,
        summary="登录（演示账号与初始密码见项目交付说明）",
    )
    async def login(payload: LoginRequest):
        # P1-1：按"用户名"全局失败锁定——换 IP/分布式爆破也会被累计。
        # 锁定返回 429（与 401 文案区分），不泄露账号是否存在。
        lock_key = f"login:user:{payload.username.lower()}"
        lock_enabled = limiter is not None and settings.rate_limit_enabled
        if lock_enabled:
            locked, retry_after = limiter.is_locked(lock_key)
            if locked:
                raise HTTPException(
                    status_code=429,
                    detail="尝试次数过多，请稍后再试",
                    headers={"Retry-After": str(retry_after)},
                )
        try:
            user = await asyncio.to_thread(
                authenticate, settings, payload.username, payload.password
            )
        except Exception as exc:  # noqa: BLE001
            # P0-2：原始异常（含 MySQL 主机/账号/错误码）只进日志，不回给客户端
            logger.exception("登录服务不可用（MySQL 连接失败）")
            raise HTTPException(
                status_code=503, detail="登录服务暂不可用，请稍后重试"
            ) from exc
        if user is None:
            if lock_enabled:
                limiter.register_failure(lock_key)
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if lock_enabled:
            limiter.clear_failures(lock_key)
        token, expires_in = auth_tokens.issue(user)
        return LoginResponse(
            token=token,
            expires_in=expires_in,
            user=UserInfo(
                id=user.id,
                username=user.username,
                nickname=user.nickname,
                role=user.role,
            ),
        )

    @router.post(
        "/auth/register",
        response_model=LoginResponse,
        status_code=201,
        summary="学生自助注册（注册成功直接返回 token，默认 student 角色）",
    )
    async def register(payload: RegisterRequest):
        try:
            user = await asyncio.to_thread(
                register_user,
                settings,
                payload.username,
                payload.password,
                payload.nickname,
            )
        except RegistrationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        except Exception as exc:  # noqa: BLE001
            # P0-2：同登录接口，原始异常只进日志
            logger.exception("注册服务不可用（MySQL 连接失败）")
            raise HTTPException(
                status_code=503, detail="注册服务暂不可用，请稍后重试"
            ) from exc
        token, expires_in = auth_tokens.issue(user)
        return LoginResponse(
            token=token,
            expires_in=expires_in,
            user=UserInfo(
                id=user.id,
                username=user.username,
                nickname=user.nickname,
                role=user.role,
            ),
        )

    @router.get(
        "/auth/me",
        response_model=UserInfo,
        summary="当前登录用户（用于前端校验 token 是否有效）",
    )
    async def me(user: AuthUser = Depends(build_current_user_dependency(settings))):
        # P0-3：改走 current_user 依赖链，接入 60s 缓存的账号活性检查
        return UserInfo(
            id=user.id, username=user.username, nickname=user.nickname, role=user.role
        )

    @router.post(
        "/auth/logout",
        response_model=LogoutResponse,
        summary="退出登录（使当前 token 失效）",
    )
    async def logout(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ):
        from app.services.auth import _extract_bearer

        auth_tokens.revoke(_extract_bearer(authorization))
        return LogoutResponse()

    return router
