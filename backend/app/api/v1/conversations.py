"""会话历史与消息反馈接口（D2）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas.conversation import (
    ConversationDetail,
    ConversationItem,
    FeedbackRequest,
    FeedbackResponse,
)
from app.services.persistence import DEMO_USER_ID


def build_router(services: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["会话"])
    persistence = services.persistence

    @router.get(
        "/conversations",
        response_model=list[ConversationItem],
        summary="会话列表（按更新时间倒序）",
    )
    async def list_conversations(limit: int = 20):
        limit = max(1, min(limit, 100))
        return await persistence.list_conversations(DEMO_USER_ID, limit)

    @router.get(
        "/conversations/{conversation_id}",
        response_model=ConversationDetail,
        summary="会话详情（含完整消息与来源引用）",
    )
    async def get_conversation(conversation_id: int):
        data = await persistence.get_conversation(conversation_id, DEMO_USER_ID)
        if data is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return data

    @router.post(
        "/messages/{message_id}/feedback",
        response_model=FeedbackResponse,
        summary="对回答点赞 / 点踩",
    )
    async def submit_feedback(message_id: int, body: FeedbackRequest):
        data = await persistence.add_feedback(
            message_id, DEMO_USER_ID, body.rating, body.comment
        )
        if data is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        return data

    return router
