"""公告模块的请求 / 响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NoticeCategory = Literal["water", "power", "network", "teaching", "life", "other"]
NoticePriority = Literal["normal", "urgent"]


class NoticeCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="公告标题")
    content: str = Field(..., min_length=1, description="公告正文")
    category: NoticeCategory = Field(default="other", description="分类：停水/停电/网络/教务/生活服务/其他")
    is_pinned: bool = Field(default=False, description="是否置顶")
    priority: NoticePriority = Field(
        default="normal", description="normal=普通；urgent=紧急，学生登录后顶部横幅提示"
    )
    status: Literal["draft", "published"] = Field(default="published", description="published=立即发布")
    effective_at: datetime | None = Field(default=None, description="生效时间，默认当前时间")
    expires_at: datetime | None = Field(default=None, description="失效时间，留空表示长期有效")


class NoticeUpdate(BaseModel):
    """编辑公告：只传需要改的字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    category: NoticeCategory | None = None
    is_pinned: bool | None = None
    priority: NoticePriority | None = None
    status: Literal["draft", "published"] | None = None
    effective_at: datetime | None = None
    expires_at: datetime | None = None


class NoticeItem(BaseModel):
    id: int
    title: str
    content: str
    category: str
    category_label: str
    is_pinned: bool = False
    priority: str = "normal"
    is_urgent: bool = False
    status: str = "published"
    publisher: str = ""
    effective_at: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    is_expired: bool = False
