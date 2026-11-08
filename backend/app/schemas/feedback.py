"""反馈与未命中工作台模型（N-25）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeedbackItem(BaseModel):
    id: int
    message_id: int
    user_id: int
    username: str | None = None
    rating: int
    rating_label: str = ""
    comment: str | None = None
    question: str | None = Field(default=None, description="该回答对应的用户提问")
    answer: str | None = Field(default=None, description="回答摘要，便于判断归因")
    attribution: str | None = None
    handle_status: str = "pending"
    status_label: str = ""
    handler_id: int | None = None
    handled_at: str | None = None
    handle_remark: str | None = None
    created_at: str | None = None


class FeedbackPage(BaseModel):
    items: list[FeedbackItem]
    total: int


class FeedbackTransitionRequest(BaseModel):
    """attribute 归因 → resolve 已补语料 → verify 已复核。"""

    action: Literal["attribute", "resolve", "verify"]
    attribution: str = Field(default="", max_length=32, description="语料缺失/答案错误/引用错误/问题不清")
    remark: str = Field(default="", max_length=500)


class UnmatchedItem(BaseModel):
    query_hash: str
    query_text: str
    count: int
    first_seen: str | None = None
    last_seen: str | None = None
    status: str = "pending"
    status_label: str = ""
    remark: str | None = None


class UnmatchedPage(BaseModel):
    items: list[UnmatchedItem]
    total: int


class UnmatchedMarkRequest(BaseModel):
    status: Literal["pending", "ignored", "resolved"]
    remark: str = Field(default="", max_length=500)
    query_text: str = Field(default="", max_length=500)
