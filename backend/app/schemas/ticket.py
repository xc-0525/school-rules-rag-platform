"""工单相关请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TicketCreateRequest(BaseModel):
    """确认提交工单（前端在草稿气泡上点"确认提交"时调用）。"""

    title: str = Field(..., min_length=1, max_length=200, description="工单标题")
    description: str = Field(..., min_length=1, max_length=2000, description="问题详情")
    category: str = Field(default="其他", max_length=50, description="分类：宿舍/食堂/图书馆/网络/其他")


class TicketCreateResponse(BaseModel):
    ticket_no: str
    status: str = "open"
    reused: bool = Field(default=False, description="true=命中幂等窗口，复用了已有工单")


class TicketItem(BaseModel):
    """工单列表/详情条目（N-01）。"""

    id: int
    ticket_no: str
    user_id: int
    username: str | None = Field(default=None, description="提交人（管理员视图）")
    title: str
    description: str
    category: str | None = None
    status: str
    status_label: str = ""
    handler_id: int | None = None
    handler_remark: str | None = Field(default=None, description="处理意见，关闭时必填")
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None


class TicketPage(BaseModel):
    items: list[TicketItem]
    total: int


class TicketTransitionRequest(BaseModel):
    """流转动作：accept 受理 / process 记录进展 / close 关闭（备注必填）。"""

    action: Literal["accept", "process", "close"]
    remark: str = Field(default="", max_length=4000)
