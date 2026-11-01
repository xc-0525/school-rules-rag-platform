"""工单相关请求/响应模型。"""

from __future__ import annotations

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
