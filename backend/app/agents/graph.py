"""Agent 图的定义：节点清单 + 边 + 条件路由（课程答辩的核心交付物）。

图的形状（10+ 节点，三种控制结构）：

    缓存查询(选择) → 查询理解 → ┬ 检索 Agent ┐
                                └ 图谱 Agent ┘(并行) → 证据融合
    → 充分性判定(选择) ──充分──→ 生成 Agent → 引用校验(选择)
         │ 不足且未超轮次                        │ 不通过且未超次数
         └→ 查询扩展(循环) ──回到并行召回         └→ 回到生成(循环)
    → 落库 / 拒答 / 降级
"""

from __future__ import annotations

from app.agents import nodes
from app.agents.runtime import NodeSpec
from app.core.config import Settings


def build_graph(settings: Settings) -> tuple[dict[str, NodeSpec], str]:
    """构建图定义，返回（节点表, 入口节点）。"""

    # ---------------- 条件路由（控制结构②：选择 + 控制结构③：循环） ----------------
    def route_cache(state) -> tuple[str | None, str]:
        if state.cached:
            return "persist_answer", "命中语义缓存，跳过完整链路"
        return "query_understand", "缓存未命中，进入完整 Agent 链路"

    def route_sufficiency(state) -> tuple[str | None, str]:
        score = state.evidence_score
        if state.evidence and score >= settings.evidence_threshold:
            return (
                "generate_answer",
                f"证据分 {score:.2f} ≥ 阈值 {settings.evidence_threshold:.2f}，证据充分",
            )
        if state.retrieval_round < settings.max_retrieval_rounds:
            return (
                "expand_query",
                f"证据不足（最高分 {score:.2f}），进入查询扩展第 {state.retrieval_round + 1} 轮",
            )
        return (
            "reject_handoff",
            f"已重试 {state.retrieval_round} 轮仍无充分证据，拒答并引导人工",
        )

    def route_grounding(state) -> tuple[str | None, str]:
        if state.grounding_ok:
            return "persist_answer", "答案引用校验通过"
        if state.regen_count <= settings.max_regen:
            return (
                "generate_answer",
                f"引用校验未通过，重新生成（第 {state.regen_count} 次）",
            )
        return "degrade_answer", f"引用校验连续未通过（{state.regen_count} 次），降级回答"

    # ---------------- 节点声明 ----------------
    specs: dict[str, NodeSpec] = {
        "cache_lookup": NodeSpec(
            name="cache_lookup",
            label="语义缓存查询",
            description="高频问题语义缓存，命中则直接返回，降低时延与成本",
            control="selection",
            run=nodes.cache_lookup,
            route=route_cache,
        ),
        "query_understand": NodeSpec(
            name="query_understand",
            label="查询理解 Agent",
            description="识别意图与实体，产出改写后的查询",
            run=nodes.query_understand,
            next="parallel_retrieval",
        ),
        "parallel_retrieval": NodeSpec(
            name="parallel_retrieval",
            label="双路并行召回",
            description="检索 Agent（Qdrant）与图谱 Agent（Neo4j）并行执行",
            kind="parallel",
            control="parallel",
            children=["retrieve_vector", "retrieve_graph"],
            next="fuse_evidence",
        ),
        "retrieve_vector": NodeSpec(
            name="retrieve_vector",
            label="检索 Agent",
            description="Qdrant 向量语义召回 Top-K 校规分块",
            run=nodes.retrieve_vector,
        ),
        "retrieve_graph": NodeSpec(
            name="retrieve_graph",
            label="图谱 Agent",
            description="Neo4j 多跳关系推理，返回实体关系路径",
            run=nodes.retrieve_graph,
        ),
        "fuse_evidence": NodeSpec(
            name="fuse_evidence",
            label="证据融合 Agent",
            description="两路证据去重、排序、生成来源引用",
            run=nodes.fuse_evidence,
            next="check_sufficiency",
        ),
        "check_sufficiency": NodeSpec(
            name="check_sufficiency",
            label="充分性判定",
            description="判断证据是否足以回答：充分则生成，不足则扩展重试，超限则拒答",
            control="selection",
            run=nodes.check_sufficiency,
            route=route_sufficiency,
        ),
        "expand_query": NodeSpec(
            name="expand_query",
            label="查询扩展 Agent",
            description="用同义词 / 实体扩展查询后重新召回（循环体）",
            control="loop",
            run=nodes.expand_query,
            next="parallel_retrieval",
        ),
        "generate_answer": NodeSpec(
            name="generate_answer",
            label="生成 Agent",
            description="DeepSeek 证据约束生成，SSE 流式输出",
            run=nodes.generate_answer,
            next="verify_grounding",
        ),
        "verify_grounding": NodeSpec(
            name="verify_grounding",
            label="引用校验 Agent",
            description="检查答案是否引用了召回证据，不通过则重新生成（循环体）",
            control="selection",
            run=nodes.verify_grounding,
            route=route_grounding,
        ),
        "reject_handoff": NodeSpec(
            name="reject_handoff",
            label="拒答 + 人工引导",
            description="多轮召回无充分证据时的兜底分支，杜绝编造",
            run=nodes.reject_handoff,
            next="persist_answer",
        ),
        "degrade_answer": NodeSpec(
            name="degrade_answer",
            label="降级回答",
            description="引用校验始终不通过时，给答案加显著提示",
            run=nodes.degrade_answer,
            next="persist_answer",
        ),
        "persist_answer": NodeSpec(
            name="persist_answer",
            label="落库 / 缓存写入",
            description="问答记录写入 MySQL，并把高频问题写入语义缓存",
            kind="terminal",
            run=nodes.persist_answer,
            next=None,
        ),
    }
    return specs, "cache_lookup"
