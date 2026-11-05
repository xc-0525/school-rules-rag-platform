"""会话历史与消息反馈接口（D2，按登录用户隔离）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings
from app.schemas.conversation import (
    ConversationDetail,
    ConversationPage,
    FeedbackRequest,
    FeedbackResponse,
)
from app.services.auth import AuthUser, build_current_user_dependency


def build_router(services: Any, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["会话"])
    persistence = services.persistence
    current_user = build_current_user_dependency(settings)

    def user_id_of(user: AuthUser) -> int:
        # 不再回落到 DEMO_USER_ID（=1，管理员）：匿名用户有自己的账号
        return user.id

    @router.get(
        "/conversations",
        response_model=ConversationPage,
        summary="会话列表（更新时间倒序，支持标题搜索与分页，仅当前用户）",
    )
    async def list_conversations(
        limit: int = 10,
        offset: int = 0,
        keyword: str = "",
        user: AuthUser = Depends(current_user),
    ):
        limit = max(1, min(limit, 50))
        offset = max(0, offset)
        keyword = (keyword or "").strip()[:50]
        items, total = await persistence.list_conversations(
            user_id_of(user), limit=limit, offset=offset, keyword=keyword
        )
        return ConversationPage(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(items) < total,
        )

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationDetail,
        summary="会话详情（含完整消息与来源引用）",
    )
    async def get_conversation(
        conversation_id: int, user: AuthUser = Depends(current_user)
    ):
        data = await persistence.get_conversation(conversation_id, user_id_of(user))
        if data is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return data

    @router.post(
        "/messages/{message_id}/feedback",
        response_model=FeedbackResponse,
        summary="对回答点赞 / 点踩",
    )
    async def submit_feedback(
        message_id: int,
        body: FeedbackRequest,
        user: AuthUser = Depends(current_user),
    ):
        data = await persistence.add_feedback(
            message_id, user_id_of(user), body.rating, body.comment
        )
        if data is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        return data

    return router
