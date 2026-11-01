"""对外 API 的请求 / 响应模型，以及贯穿工作流的证据模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """问答请求。"""

    query: str = Field(..., min_length=1, max_length=500, description="学生问题", examples=["宿舍晚上几点熄灯？"])
    stream: bool = Field(default=True, description="True=SSE 流式（含 Agent 轨迹）；False=一次性 JSON")
    conversation_id: int | None = Field(default=None, description="多轮会话 ID，首轮留空")


class Evidence(BaseModel):
    """一条召回证据（向量分块或图谱路径），贯穿 Agent 图。"""

    type: Literal["vector", "graph", "tool"] = Field(
        description="vector=向量召回，graph=图谱关系，tool=业务工具结果"
    )
    title: str = Field(description="来源名称，如《学生宿舍管理规定》")
    detail: str = Field(description="章节 / 路径 ID")
    snippet: str = Field(description="证据原文")
    score: float = Field(default=0.0, description="相似度 / 置信度")


class SourceItem(BaseModel):
    """返回给前端的来源引用（不含内部字段）。"""

    type: Literal["vector", "graph", "tool"]
    title: str
    detail: str
    snippet: str


class ChatResponse(BaseModel):
    """非流式返回结构（stream=false 时使用）。"""

    query: str
    answer: str
    sources: list[SourceItem] = Field(default_factory=list)
    refused: bool = False
    degraded: bool = False
    cached: bool = False
    grounding_status: str = Field(
        default="unverified",
        description="引用校验状态：verified=通过；unverified=未通过；skipped=工具直答未参与校验",
    )
    grounding_reason: str = ""
    llm_failed: bool = Field(
        default=False, description="true=大模型不可用（未配置 key 或调用失败），答案已降级"
    )
    sufficiency_verdict: str = Field(
        default="",
        description="证据充分性判定：sufficient=可答；insufficient=条款未回答问题（拒答）；skipped=未判定",
    )
    sufficiency_reason: str = ""
    request_id: str = Field(default="", description="本次请求的追踪 id（同时出现在响应头 X-Request-ID）")
    conversation_id: int | None = None
    message_id: int | None = Field(default=None, description="助手消息 id，前端点赞点踩可直接用")
    tool_drafts: list[dict[str, Any]] = Field(
        default_factory=list, description="待确认的工单草稿（前端渲染确认按钮）"
    )
    runtime: dict[str, Any] = Field(default_factory=dict, description="图引擎运行信息（步数/节点/循环次数）")
