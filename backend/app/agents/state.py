"""Agent 工作流共享状态（State）。

图引擎在每个节点之间传递同一个 AgentState 的副本；
节点只返回"要修改的字段"，由引擎合并。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.chat import Evidence, SourceItem


class AgentState(BaseModel):
    """一次问答在 Agent 图中的完整状态。"""

    query: str
    conversation_id: int | None = None
    user_id: int = 1
    run_id: str = ""

    # ---- 多轮记忆（最近消息注入 Prompt）----
    history: list[dict[str, str]] = Field(default_factory=list)

    # ---- Tool Calling（LLM 选择工具 + 执行结果）----
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_evidence: list[Evidence] = Field(default_factory=list)
    tool_search_used: bool = False
    tool_only: bool = False

    # ---- 查询理解 ----
    intent: str = ""
    rewritten_query: str = ""
    entities: list[str] = Field(default_factory=list)

    # ---- 循环控制计数 ----
    retrieval_round: int = 0   # 证据不足触发的检索扩展轮次
    regen_count: int = 0       # 引用校验失败触发的重新生成次数

    # ---- 召回与证据 ----
    vector_hits: list[Evidence] = Field(default_factory=list)
    graph_paths: list[Evidence] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_score: float = 0.0
    sources: list[SourceItem] = Field(default_factory=list)

    # ---- 生成与校验 ----
    answer: str = ""
    grounding_ok: bool = False
    refused: bool = False
    degraded: bool = False
    cached: bool = False

    # ---- 运行信息 ----
    steps: int = 0
    latency_ms: int = 0
