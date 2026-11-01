"""Agent 工作流共享状态（State）。

图引擎在每个节点之间传递同一个 AgentState 的副本；
节点只返回"要修改的字段"，由引擎合并。

P0-4 / P0-5 的字段约定：
- `generation_query`：给大模型看的自然问句（原问题或多轮改写句），**不含**检索扩展词；
- `retrieval_query` ：给向量/图谱用的检索串（可能被 expand_query 追加同义词）；
- `grounding_status`：三态引用校验结果，`skipped` 专指"工具结果直答、未参与引用校验"，
  不能被当成 `verified` 写缓存或计入评测分子。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.chat import Evidence, SourceItem

GroundingStatus = Literal["verified", "unverified", "skipped"]


class AgentState(BaseModel):
    """一次问答在 Agent 图中的完整状态。"""

    query: str
    conversation_id: int | None = None
    user_id: int = 0
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
    entities: list[str] = Field(default_factory=list)
    # 给大模型的问题（自然语言，不含检索扩展词）
    generation_query: str = ""
    # 给检索用的串（可能被扩展）
    retrieval_query: str = ""
    # 查询扩展实际追加的词；为空表示"扩不出来"，路由应直接拒答而不是空跑
    expansion_terms: list[str] = Field(default_factory=list)
    expansion_exhausted: bool = False

    # ---- 循环控制计数 ----
    retrieval_round: int = 0   # 证据不足触发的检索扩展轮次
    regen_count: int = 0       # 引用校验失败触发的重新生成次数

    # ---- 召回与证据 ----
    vector_hits: list[Evidence] = Field(default_factory=list)
    graph_paths: list[Evidence] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_score: float = 0.0
    evidence_meta: dict[str, Any] = Field(default_factory=dict)
    sources: list[SourceItem] = Field(default_factory=list)

    # ---- 生成与校验 ----
    answer: str = ""
    grounding_status: GroundingStatus = "unverified"
    grounding_reason: str = ""
    refused: bool = False
    degraded: bool = False
    cached: bool = False

    # ---- 落库回执（前端点赞点踩直接用，省掉一次会话详情请求）----
    message_id: int | None = None

    # ---- 运行信息 ----
    steps: int = 0
    latency_ms: int = 0
