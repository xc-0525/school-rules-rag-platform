"""登录 / 当前用户 / 退出接口。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.config import Settings
from app.schemas.auth import LoginRequest, LoginResponse, LogoutResponse, UserInfo
from app.services.auth import (
    AuthUser,
    auth_tokens,
    authenticate,
    require_user,
)


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["鉴权"])

    @router.post(
        "/auth/login",
        response_model=LoginResponse,
        summary="登录（admin/admin123 或 student/student123）",
    )
    async def login(payload: LoginRequest):
        try:
            user = await asyncio.to_thread(
                authenticate, settings, payload.username, payload.password
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail=f"登录服务暂不可用（MySQL 连接失败）：{exc}"
            ) from exc
        if user is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
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
    async def me(user: AuthUser = Depends(require_user)):
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
