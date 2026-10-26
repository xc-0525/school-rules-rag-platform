"""Agent 图中的节点实现。

每个节点是 `async def (state, ctx) -> dict`：
- 只返回要修改的状态字段，由图引擎合并；
- 需要流式输出时通过 `ctx.delta()` 推送；
- 选择/循环的控制逻辑放在 `graph.py` 的路由函数里，节点本身保持纯粹。
"""

from __future__ import annotations

from typing import Any

from app.agents.runtime import NodeContext
from app.agents.state import AgentState
from app.schemas.chat import SourceItem
from app.services import mock_data

NO_EVIDENCE_ANSWER = (
    "抱歉，未找到相关校规。建议换一种说法重新提问，或联系辅导员 / 学生工作处进行人工确认。"
)
DEGRADE_NOTE = "\n\n（说明：本答案未通过引用校验，仅供参考，请以《学生手册》原文为准。）"

SYSTEM_PROMPT = (
    "你是高校『校纪校规』智能问答助手。必须严格遵守：\n"
    "1. 只能依据【背景资料】作答，禁止编造条款编号、处分等级、办理流程；\n"
    "2. 若【背景资料】不足以回答问题，直接回答“抱歉，未找到相关校规”，"
    "并建议咨询辅导员或学生工作处；\n"
    "3. 回答使用简体中文：先给结论，再逐条列出依据（注明文档名称与章节）；\n"
    "4. 语气客观严谨，不使用“应该”“大概”等模糊措辞。"
)
STRICT_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n5. 这是重新生成：上一版答案未通过引用校验，本轮必须逐条引用【背景资料】中的原文。"
)


def build_messages(state: AgentState, strict: bool = False) -> list[dict[str, str]]:
    """组装证据约束 Prompt。"""
    blocks = [
        f"[{index}] {item.title} · {item.detail}（{item.type}，相似度 {item.score:.2f}）\n{item.snippet}"
        for index, item in enumerate(state.evidence, start=1)
    ]
    context = "\n\n".join(blocks) if blocks else "（无可用资料）"
    return [
        {"role": "system", "content": STRICT_SYSTEM_PROMPT if strict else SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"【背景资料】\n{context}\n\n"
                f"【用户问题】\n{state.query}\n\n"
                "请严格依据【背景资料】回答，并在末尾注明引用条款。"
            ),
        },
    ]


def _sources_payload(state: AgentState) -> list[dict[str, Any]]:
    return [source.model_dump() for source in state.sources]


# ---------------------------------------------------------------------------
# ① 语义缓存查询（选择结构：命中直接短路到落库）
# ---------------------------------------------------------------------------
async def cache_lookup(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    hit = ctx.services.cache.lookup(state.query)
    if hit is None:
        return {"cached": False}

    answer, sources = hit
    await ctx.emit("meta", {"sources": [source.model_dump() for source in sources], "cached": True})
    await ctx.emit("delta", {"content": answer})
    return {"cached": True, "answer": answer, "sources": sources}


# ---------------------------------------------------------------------------
# ② 查询理解 Agent
# ---------------------------------------------------------------------------
async def query_understand(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    intent = "其他"
    entities: list[str] = []
    for name, keywords in mock_data.INTENT_KEYWORDS.items():
        matched = [keyword for keyword in keywords if keyword in state.query]
        if matched:
            intent = name
            entities.extend(matched)
    if not entities:
        entities = [state.query]
    return {
        "intent": intent,
        "entities": list(dict.fromkeys(entities)),
        "rewritten_query": state.query,
    }


# ---------------------------------------------------------------------------
# ③④ 并行召回：检索 Agent（Qdrant）/ 图谱 Agent（Neo4j）
# ---------------------------------------------------------------------------
async def retrieve_vector(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    hits = await ctx.services.vector.search(state.rewritten_query, top_k=3)
    return {"vector_hits": hits}


async def retrieve_graph(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    paths = await ctx.services.graph.search_paths(state.rewritten_query, top_k=2)
    return {"graph_paths": paths}


# ---------------------------------------------------------------------------
# ⑤ 证据融合 Agent
# ---------------------------------------------------------------------------
async def fuse_evidence(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    seen: set[tuple[str, str, str]] = set()
    merged = []
    for item in [*state.vector_hits, *state.graph_paths]:
        key = (item.type, item.title, item.detail)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    merged.sort(key=lambda item: item.score, reverse=True)

    sources = [
        SourceItem(
            type=item.type,
            title=item.title,
            detail=item.detail,
            snippet=item.snippet,
        )
        for item in merged
    ]
    score = max((item.score for item in merged), default=0.0)
    await ctx.emit("meta", {"sources": [source.model_dump() for source in sources], "cached": False})
    return {"evidence": merged, "evidence_score": score, "sources": sources}


# ---------------------------------------------------------------------------
# ⑥ 充分性判定（选择结构：充分 / 扩展重试 / 拒答）
# ---------------------------------------------------------------------------
async def check_sufficiency(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    # 评分在融合节点算好；这里保持为一个显式节点，便于前端展示"判定"步骤
    return {}


# ---------------------------------------------------------------------------
# ⑦ 查询扩展（循环结构：回到并行召回）
# ---------------------------------------------------------------------------
async def expand_query(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    round_no = state.retrieval_round + 1
    extras: list[str] = []
    for phrase, synonyms in mock_data.EXPANSIONS.items():
        if phrase in state.rewritten_query:
            extras.extend(synonyms)
    if not extras:
        extras = [entity for entity in state.entities if entity not in state.rewritten_query][:3]

    query = state.rewritten_query
    for extra in dict.fromkeys(extras):
        if extra not in query:
            query = f"{query} {extra}"
    return {"rewritten_query": query, "retrieval_round": round_no}


# ---------------------------------------------------------------------------
# ⑧ 生成 Agent（DeepSeek 流式，证据约束）
# ---------------------------------------------------------------------------
async def generate_answer(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    messages = build_messages(state, strict=state.regen_count > 0)
    pieces: list[str] = []
    async for piece in ctx.services.llm.stream(messages):
        pieces.append(piece)
        await ctx.delta(piece)
    return {"answer": "".join(pieces)}


# ---------------------------------------------------------------------------
# ⑨ 引用校验 Agent（选择 + 循环：不通过则重新生成）
# ---------------------------------------------------------------------------
async def verify_grounding(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    answer = state.answer
    titles = [item.title for item in state.evidence]
    cited = any(title in answer for title in titles)
    ok = cited and len(answer) >= 10
    if ok:
        return {"grounding_ok": True}
    return {"grounding_ok": False, "regen_count": state.regen_count + 1}


# ---------------------------------------------------------------------------
# ⑩ 拒答 / 降级 / 落库
# ---------------------------------------------------------------------------
async def reject_handoff(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    await ctx.emit("meta", {"sources": [], "cached": False, "refused": True})
    await ctx.delta(NO_EVIDENCE_ANSWER)
    return {"answer": NO_EVIDENCE_ANSWER, "refused": True, "sources": []}


async def degrade_answer(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    await ctx.delta(DEGRADE_NOTE)
    return {"answer": state.answer + DEGRADE_NOTE, "degraded": True}


async def persist_answer(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    conversation_id = await ctx.services.persistence.save_qa(state)
    if not state.cached and not state.refused and state.answer:
        ctx.services.cache.store(state.query, state.answer, state.sources)
    return {"conversation_id": conversation_id}
