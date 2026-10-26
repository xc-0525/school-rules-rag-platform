"""会话 / 消息 / 反馈相关的 API 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.chat import SourceItem


class ConversationItem(BaseModel):
    """会话列表项。"""

    id: int
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0


class MessageItem(BaseModel):
    """会话中的一条消息。"""

    id: int
    conversation_id: int
    role: str
    content: str
    sources: list[SourceItem] = Field(default_factory=list)
    refused: bool = False
    degraded: bool = False
    latency_ms: int = 0
    created_at: str | None = None


class ConversationDetail(BaseModel):
    """会话详情：包含完整消息列表。"""

    id: int
    user_id: int
    title: str
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0
    messages: list[MessageItem] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    """点赞 / 点踩。"""

    rating: int = Field(..., ge=-1, le=1, description="1=赞，-1=踩")
    comment: str | None = Field(default=None, max_length=500)


class FeedbackResponse(BaseModel):
    """反馈结果。"""

    id: int
    message_id: int
    rating: int
    comment: str | None = None
