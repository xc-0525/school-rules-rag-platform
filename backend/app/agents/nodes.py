"""Agent 图中的节点实现（编排层）。

第6步重构：把三块独立职责移到同级模块，这里只保留"图上有什么节点"：
- prompts.py     系统提示词与固定文案
- prompting.py   Prompt 组装（多轮历史 + 证据约束）
- tools_node.py  Tool Calling 节点（原 188 行）
- fusion.py      证据融合（RRF + 质量闸门 + 占比上限）
- citations.py   引用校验（三态 + 条款号核对）

对外名称保持不变：`from app.agents import nodes` 之后 nodes.cache_lookup /
nodes.tool_agent / nodes.fuse_evidence / nodes.verify_grounding 等仍可直接用，
graph.py 与测试无需改动。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.agents.citations import (  # noqa: F401 —— 对外兼容再导出
    ARTICLE_RE,
    GRAPH_TITLE_PREFIX,
    _clean_title,
    _collect_evidence_citations,
    verify_grounding,
)
from app.agents.fusion import _evidence_key, fuse_evidence  # noqa: F401
from app.agents.prompting import (  # noqa: F401
    _history_messages,
    _prompt_question,
    _search_text,
    _sources_payload,
    build_messages,
)
from app.agents.prompts import (  # noqa: F401
    DEGRADE_NOTE,
    LLM_UNAVAILABLE_ANSWER,
    NO_EVIDENCE_ANSWER,
    STRICT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    TOOL_SYSTEM_PROMPT,
)
from app.agents.runtime import NodeContext
from app.schemas.chat import SourceItem
from app.agents.state import AgentState
from app.agents.tools_node import tool_agent  # noqa: F401
from app.services import lexicon

logger = logging.getLogger("app.agents")

async def cache_lookup(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    hit = ctx.services.cache.lookup(state.query)
    if hit is None:
        return {"cached": False}

    answer, sources = hit
    await ctx.emit("meta", {"sources": [source.model_dump() for source in sources], "cached": True})
    await ctx.delta(answer)
    return {"cached": True, "answer": answer, "sources": sources}

async def query_understand(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    """识别意图与实体，并初始化两条查询串。

    - generation_query：给大模型看的自然问句（后续只可能被多轮改写覆盖）；
    - retrieval_query ：给检索用的串（后续可能被 expand_query 追加同义词）。
    """
    intent, entities = lexicon.extract_intent(state.query)
    return {
        "intent": intent,
        "entities": entities,
        "generation_query": state.generation_query or state.query,
        "retrieval_query": state.retrieval_query or state.query,
    }

FOLLOWUP_RE = re.compile(
    r"^(那|那么|这个|那个|它|他|她|该|上面|前面|还有|继续)|"
    r"(第二|第三|第四|最后)个?[条章项]|呢[?？]?$"
)

def _is_followup(query: str) -> bool:
    query = query.strip()
    return bool(FOLLOWUP_RE.search(query)) or len(query) <= 6

async def memory_agent(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """加载最近历史；对指代型追问先改写为独立 query 再进入检索。"""
    history: list[dict[str, str]] = []
    if state.conversation_id:
        try:
            history = await ctx.services.persistence.recent_messages(
                state.conversation_id,
                state.user_id,
                limit=ctx.settings.memory_window,
            )
        except Exception:  # noqa: BLE001 —— 记忆加载失败不阻断主流程
            logger.exception("加载多轮记忆失败")

    rewritten = state.query
    if history and _is_followup(state.query):
        try:
            rewritten = await ctx.services.llm.rewrite_query(history, state.query)
        except Exception:  # noqa: BLE001
            logger.exception("追问改写失败，使用原始 query")
    # 改写结果同时作为生成与检索的起点；扩展只允许改 retrieval_query
    return {"history": history, "generation_query": rewritten, "retrieval_query": rewritten}

async def retrieve_vector(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    hits = await ctx.services.vector.search(_search_text(state), top_k=3)
    return {"vector_hits": hits}

async def retrieve_graph(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    paths = await ctx.services.graph.search_paths(_search_text(state), top_k=2)
    return {"graph_paths": paths}

async def check_sufficiency(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """判断召回条款是否真的能回答问题（拒答判定的核心）。

    历史：这里曾是空实现，拒答完全依赖 evidence_score 与固定阈值。
    真实语料实测定标后确认该做法不成立——可答题最低向量分 0.567、
    应拒答题最高 0.620，两者区间重叠，阈值调到哪都会同时误杀和漏放。

    现在输出三态，供 route_sufficiency 决策：
    - sufficient  ：条款直接回答了问题（高置信度预筛通过，或模型判定通过）
    - insufficient：只是沾边，或问题涉及的主体压根没出现在条款里
    - skipped     ：判定关闭 / 调用失败，退回旧的阈值逻辑（宁可答也不误拒）

    成本控制（auto 模式）：只有分数落在 [low, high) 灰区才真的调模型，
    两端用阈值直接裁决，避免每次问答都多付一次调用。
    """
    if not state.evidence:
        return {
            "sufficiency_verdict": "insufficient",
            "sufficiency_reason": "未召回任何条款",
        }

    mode = (ctx.settings.sufficiency_judge or "auto").lower()
    score = state.evidence_score

    if mode == "off":
        return {
            "sufficiency_verdict": "skipped",
            "sufficiency_reason": "判定已关闭，按相似度阈值放行",
        }
    if mode == "auto":
        high = ctx.settings.sufficiency_judge_high
        low = ctx.settings.sufficiency_judge_low
        if score >= high:
            return {
                "sufficiency_verdict": "sufficient",
                "sufficiency_reason": f"向量分 {score:.2f} ≥ {high:.2f}，直接放行",
            }
        if score < low:
            return {
                "sufficiency_verdict": "insufficient",
                "sufficiency_reason": f"向量分 {score:.2f} < {low:.2f}，未达最低相关度",
            }

    judge = getattr(ctx.services.llm, "judge_sufficiency", None)
    if judge is None:
        return {
            "sufficiency_verdict": "skipped",
            "sufficiency_reason": "当前模型不支持充分性判定",
        }

    # 判定候选不能按融合顺序截断：append 融合会保持检索顺序，首轮召回的
    # 低质条款排在前面，真正能回答问题的条款可能在第 5 条之后（实测
    # "考试带手机" 一题，41 号令第五十二条就因此被切掉，导致误拒答）。
    # 这里按相关度取前 6 条交给判定。
    candidates = sorted(state.evidence, key=lambda item: item.score, reverse=True)[:6]
    payload = [
        {"title": item.title, "detail": item.detail, "snippet": item.snippet}
        for item in candidates
    ]
    try:
        result = await judge(state.query, payload)
    except Exception as exc:  # noqa: BLE001 —— 判定失败不能阻断问答
        logger.warning("充分性判定失败，放行：%s", exc)
        return {
            "sufficiency_verdict": "skipped",
            "sufficiency_reason": f"判定调用异常（{type(exc).__name__}），按阈值放行",
        }

    verdict = "sufficient" if result.get("sufficient") else "insufficient"
    return {
        "sufficiency_verdict": verdict,
        "sufficiency_reason": str(result.get("reason") or "")[:120],
    }

async def expand_query(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """用实体同义词 / 口语说法扩展检索串。

    与旧实现的关键差异：旧代码从 entities 里反向过滤（`entity not in query`），
    而 entities 按构造必然是 query 的子串，于是兜底恒为空、整个循环空转。
    现在改为主动查同义词表，扩不出词时置 `expansion_exhausted=True`，
    由路由直接拒答，不再白跑一轮 Qdrant + Neo4j。
    """
    round_no = state.retrieval_round + 1
    base = _search_text(state)
    limit = max(1, int(getattr(ctx.settings, "query_expansion_max_terms", 3)))
    expanded, terms = lexicon.expand_query(base, state.entities, limit=limit)
    if not terms:
        return {
            "retrieval_round": round_no,
            "expansion_terms": [],
            "expansion_exhausted": True,
        }
    return {
        "retrieval_query": expanded,
        "expansion_terms": terms,
        "expansion_exhausted": False,
        "retrieval_round": round_no,
    }

async def generate_answer(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    # P1-5：重新生成前先清空前端已渲染的上一版草稿，否则两版答案会拼在一起
    if state.regen_count > 0 and state.answer:
        await ctx.emit(
            "reset",
            {
                "reason": "上一版答案未通过引用校验，正在重新生成",
                "regen_count": state.regen_count,
            },
        )
    messages = build_messages(state, strict=state.regen_count > 0)
    pieces: list[str] = []
    try:
        async for piece in ctx.services.llm.stream(messages):
            pieces.append(piece)
            await ctx.delta(piece)
    except Exception as exc:  # noqa: BLE001 —— 大模型不可用不能吞掉整次问答
        logger.exception("大模型生成失败，降级为提示文案并继续落库")
        note = LLM_UNAVAILABLE_ANSWER
        if state.evidence:
            note = (
                f"{note}\n\n（本次已检索到 {len(state.evidence)} 条相关条款，可在下方来源中查看。）"
            )
        await ctx.delta(note)
        return {
            "answer": note,
            "llm_failed": True,
            "llm_error": f"{type(exc).__name__}: {exc}"[:200],
            # 标记降级：前端会显示"降级回答"，同时这条答案不会被写入缓存
            "degraded": True,
        }
    return {"answer": "".join(pieces)}

async def reject_handoff(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """拒答，但保留检索到的相近条款。

    以前这里返回空来源，用户只看到"未找到"，无法区分是
    "知识库确实没有" 还是 "检索跑偏/判定过严"。现在把已召回的
    前 3 条一并带回，前端标注为"未采用"，用户能直接看出检索到了什么。
    """
    fallback = [
        SourceItem(type=item.type, title=item.title, detail=item.detail, snippet=item.snippet)
        for item in sorted(state.evidence, key=lambda entry: entry.score, reverse=True)[:3]
    ]
    await ctx.emit(
        "meta",
        {"sources": [item.model_dump() for item in fallback], "cached": False, "refused": True},
    )
    await ctx.delta(NO_EVIDENCE_ANSWER)
    return {"answer": NO_EVIDENCE_ANSWER, "refused": True, "sources": fallback}

async def degrade_answer(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    await ctx.delta(DEGRADE_NOTE)
    return {"answer": state.answer + DEGRADE_NOTE, "degraded": True}

async def persist_answer(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    receipt = await ctx.services.persistence.save_qa(state)
    conversation_id = receipt.get("conversation_id") if isinstance(receipt, dict) else receipt
    message_id = receipt.get("assistant_message_id") if isinstance(receipt, dict) else None
    # 只缓存"通过引用校验"的正常答案：拒答、降级、缓存命中的结果都不写缓存；
    # tool_only 的 grounding_status 是 skipped，天然被排除（P0-4）。
    if (
        not state.cached
        and not state.refused
        and not state.degraded
        and not state.tool_only
        and state.grounding_status == "verified"
        and state.answer
    ):
        ctx.services.cache.store(state.query, state.answer, state.sources)
    return {"conversation_id": conversation_id, "message_id": message_id}
