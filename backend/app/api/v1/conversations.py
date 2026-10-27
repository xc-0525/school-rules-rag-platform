"""会话历史与消息反馈接口（D2，按登录用户隔离）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings
from app.schemas.conversation import (
    ConversationDetail,
    ConversationItem,
    FeedbackRequest,
    FeedbackResponse,
)
from app.services.auth import AuthUser, build_current_user_dependency
from app.services.persistence import DEMO_USER_ID


def build_router(services: Any, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["会话"])
    persistence = services.persistence
    current_user = build_current_user_dependency(settings)

    def user_id_of(user: AuthUser | None) -> int:
        return user.id if user else DEMO_USER_ID

    @router.get(
        "/conversations",
        response_model=list[ConversationItem],
        summary="会话列表（按更新时间倒序，仅当前用户）",
    )
    async def list_conversations(
        limit: int = 20, user: AuthUser | None = Depends(current_user)
    ):
        limit = max(1, min(limit, 100))
        return await persistence.list_conversations(user_id_of(user), limit)

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationDetail,
        summary="会话详情（含完整消息与来源引用）",
    )
    async def get_conversation(
        conversation_id: int, user: AuthUser | None = Depends(current_user)
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
        user: AuthUser | None = Depends(current_user),
    ):
        data = await persistence.add_feedback(
            message_id, user_id_of(user), body.rating, body.comment
        )
        if data is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        return data

    return router
