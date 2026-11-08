"""用户账号管理接口（仅管理员）。

为什么单独一个模块而不是塞进 auth.py：auth 只负责"自己是谁"（登录/注册/me/登出），
这里是"管理员管别人"，鉴权口径、审计与护栏都不同，混在一起会让 auth 变成上帝对象。

沿用项目既有口径：DEV_MODE 下放行（与公告、文档模块一致，便于本地调试）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings
from app.schemas.user import ResetPasswordRequest, UserPage, UserUpdateRequest
from app.services import users as user_service
from app.services.auth import AuthUser, build_current_user_dependency

logger = logging.getLogger("app.users")


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["用户管理"])
    current_user = build_current_user_dependency(settings)

    def ensure_admin(user: AuthUser) -> None:
        if settings.dev_mode:
            return
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可管理用户账号")

    def _translate(exc: user_service.UserAdminError) -> HTTPException:
        return HTTPException(status_code=exc.status_code, detail=exc.message)

    @router.get("/users", response_model=UserPage, summary="用户列表（仅管理员）")
    async def list_users_api(
        keyword: str = Query(default="", description="用户名/昵称关键词"),
        role: str = Query(default="", description="角色过滤：student / admin"),
        status: int | None = Query(default=None, description="状态过滤：1 启用 / 0 禁用"),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        user: AuthUser = Depends(current_user),
    ):
        ensure_admin(user)
        try:
            return await asyncio.to_thread(
                lambda: user_service.list_users(
                    settings,
                    keyword=keyword,
                    role=role,
                    status=status,
                    limit=limit,
                    offset=offset,
                )
            )
        except user_service.UserAdminError as exc:
            raise _translate(exc) from exc

    @router.patch("/users/{user_id}", summary="修改用户昵称/角色/状态（仅管理员）")
    async def update_user_api(
        user_id: int,
        payload: UserUpdateRequest,
        user: AuthUser = Depends(current_user),
    ):
        ensure_admin(user)
        try:
            result = await asyncio.to_thread(
                lambda: user_service.update_user(
                    settings,
                    actor_id=user.id,
                    user_id=user_id,
                    nickname=payload.nickname,
                    role=payload.role,
                    status=payload.status,
                )
            )
        except user_service.UserAdminError as exc:
            raise _translate(exc) from exc
        logger.info(
            "管理员改用户 actor=%s target=%s nickname=%s role=%s status=%s",
            user.id,
            user_id,
            payload.nickname,
            payload.role,
            payload.status,
        )
        return result

    @router.post("/users/{user_id}/reset-password", summary="重置用户密码（仅管理员）")
    async def reset_password_api(
        user_id: int,
        payload: ResetPasswordRequest,
        user: AuthUser = Depends(current_user),
    ):
        ensure_admin(user)
        try:
            result = await asyncio.to_thread(
                lambda: user_service.reset_password(
                    settings,
                    actor_id=user.id,
                    user_id=user_id,
                    new_password=payload.new_password,
                )
            )
        except user_service.UserAdminError as exc:
            raise _translate(exc) from exc
        logger.info("管理员重置密码 actor=%s target=%s", user.id, user_id)
        return result

    return router
