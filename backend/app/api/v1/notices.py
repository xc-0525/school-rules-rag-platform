"""校园公告接口：浏览（登录即可）+ 管理（仅管理员）。

与问答的关系：`agents/tools.py` 的 `query_announcements` 调用同一套
`services/notices` 函数，保证"页面上看不到的公告，问答也答不出来"。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings
from app.schemas.notice import NoticeCreate, NoticeItem, NoticeUpdate
from app.services import notices as notice_service
from app.services.auth import AuthUser, build_current_user_dependency


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["校园公告"])
    current_user = build_current_user_dependency(settings)

    def ensure_admin(user: AuthUser) -> None:
        """发布/编辑/删除仅管理员；DEV_MODE 下放行（与文档模块同一口径）。"""
        if settings.dev_mode:
            return
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可管理校园公告")

    @router.get("/notices", response_model=list[NoticeItem], summary="公告列表")
    async def list_notices_api(
        category: str = Query(default="", description="分类过滤，留空为全部"),
        keyword: str = Query(default="", description="标题/正文关键词"),
        include_expired: bool = Query(default=False, description="是否包含已过期公告"),
        limit: int = Query(default=50, ge=1, le=200),
        user: AuthUser = Depends(current_user),
    ):
        return await asyncio.to_thread(
            lambda: notice_service.list_notices(
                settings,
                category=category,
                keyword=keyword,
                include_expired=include_expired,
                # 管理员能看到草稿，学生只能看到已发布
                include_draft=user.is_admin,
                limit=limit,
            )
        )

    @router.get("/notices/meta/categories", summary="公告分类字典")
    async def categories_api(user: AuthUser = Depends(current_user)):  # noqa: ARG001
        return [
            {"code": code, "label": label}
            for code, label in notice_service.NOTICE_CATEGORIES.items()
        ]

    @router.get("/notices/{notice_id}", response_model=NoticeItem, summary="公告详情")
    async def get_notice_api(notice_id: int, user: AuthUser = Depends(current_user)):
        notice = await asyncio.to_thread(notice_service.get_notice, settings, notice_id)
        if notice is None:
            raise HTTPException(status_code=404, detail="公告不存在")
        if notice.get("status") != "published" and not user.is_admin:
            raise HTTPException(status_code=404, detail="公告不存在")
        return notice

    @router.post(
        "/notices", response_model=NoticeItem, status_code=201, summary="发布公告（管理员）"
    )
    async def create_notice_api(payload: NoticeCreate, user: AuthUser = Depends(current_user)):
        ensure_admin(user)
        try:
            return await asyncio.to_thread(
                notice_service.create_notice, settings, user.id, payload.model_dump()
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/notices/{notice_id}", response_model=NoticeItem, summary="编辑公告（管理员）")
    async def update_notice_api(
        notice_id: int, payload: NoticeUpdate, user: AuthUser = Depends(current_user)
    ):
        ensure_admin(user)
        # exclude_unset：只更新前端显式给出的字段，避免把未传的字段清空
        updates: dict[str, Any] = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")
        notice = await asyncio.to_thread(
            notice_service.update_notice, settings, notice_id, updates
        )
        if notice is None:
            raise HTTPException(status_code=404, detail="公告不存在")
        return notice

    @router.delete("/notices/{notice_id}", status_code=204, summary="删除公告（管理员）")
    async def delete_notice_api(notice_id: int, user: AuthUser = Depends(current_user)):
        ensure_admin(user)
        ok = await asyncio.to_thread(notice_service.delete_notice, settings, notice_id)
        if not ok:
            raise HTTPException(status_code=404, detail="公告不存在")

    return router
