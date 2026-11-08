"""反馈与未命中工作台接口（N-25，仅管理员）。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings
from app.schemas.feedback import (
    FeedbackPage,
    FeedbackTransitionRequest,
    UnmatchedMarkRequest,
    UnmatchedPage,
)
from app.services import feedback_admin as fb_service
from app.services.auth import AuthUser, build_current_user_dependency


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["反馈工作台"])
    current_user = build_current_user_dependency(settings)

    def ensure_admin(user: AuthUser) -> None:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可访问")

    def _raise(exc: fb_service.FeedbackRuleError) -> None:
        # 内存模式没有 feedback 表，属于依赖未就绪，用 503 区别于业务规则 400
        status_code = 503 if "MySQL" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.get("/feedback", response_model=FeedbackPage, summary="反馈列表（归因与处理状态）")
    async def list_feedback_api(
        rating: int = Query(default=0, description="1=点赞 -1=点踩 0=全部"),
        status: str = Query(default="", description="pending/attributed/resolved/verified"),
        keyword: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        user: AuthUser = Depends(current_user),
    ) -> Any:
        ensure_admin(user)
        try:
            items, total = await asyncio.to_thread(
                fb_service.list_feedback,
                settings,
                rating=rating,
                status=status,
                keyword=keyword,
                limit=limit,
                offset=offset,
            )
        except fb_service.FeedbackRuleError as exc:
            _raise(exc)
        return FeedbackPage(items=items, total=total)

    @router.post(
        "/feedback/{feedback_id}/transition",
        summary="反馈流转（归因/已补语料/已复核）",
    )
    async def transition_feedback_api(
        feedback_id: int,
        payload: FeedbackTransitionRequest,
        user: AuthUser = Depends(current_user),
    ) -> Any:
        ensure_admin(user)
        try:
            result = await asyncio.to_thread(
                fb_service.transition_feedback,
                settings,
                feedback_id,
                user.id,
                action=payload.action,
                attribution=payload.attribution,
                remark=payload.remark,
            )
        except fb_service.FeedbackRuleError as exc:
            _raise(exc)
        if result is None:
            raise HTTPException(status_code=404, detail="反馈不存在")
        return result

    @router.get("/unmatched", response_model=UnmatchedPage, summary="未命中问题聚合")
    async def list_unmatched_api(
        days: int = Query(default=30, ge=1, le=365),
        status: str = Query(default="", description="pending/ignored/resolved"),
        keyword: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        user: AuthUser = Depends(current_user),
    ) -> Any:
        ensure_admin(user)
        try:
            items, total = await asyncio.to_thread(
                fb_service.list_unmatched,
                settings,
                days=days,
                status=status,
                keyword=keyword,
                limit=limit,
                offset=offset,
            )
        except fb_service.FeedbackRuleError as exc:
            _raise(exc)
        return UnmatchedPage(items=items, total=total)

    @router.post("/unmatched/{query_hash}/mark", summary="标记未命中问题（已忽略/已补语料）")
    async def mark_unmatched_api(
        query_hash: str,
        payload: UnmatchedMarkRequest,
        user: AuthUser = Depends(current_user),
    ) -> Any:
        ensure_admin(user)
        try:
            return await asyncio.to_thread(
                fb_service.mark_unmatched,
                settings,
                digest=query_hash,
                query_text=payload.query_text,
                status=payload.status,
                remark=payload.remark,
                actor_id=user.id,
            )
        except fb_service.FeedbackRuleError as exc:
            _raise(exc)

    return router
