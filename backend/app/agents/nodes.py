"""Agent 图中的节点实现。

每个节点是 `async def (state, ctx) -> dict`：
- 只返回要修改的状态字段，由图引擎合并；
- 需要流式输出时通过 `ctx.delta()` 推送；
- 选择/循环的控制逻辑放在 `graph.py` 的路由函数里，节点本身保持纯粹。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from app.agents.runtime import NodeContext
from app.agents.state import AgentState
from app.agents.tools import TOOL_DEFINITIONS, ToolRunner
from app.schemas.chat import Evidence, SourceItem
from app.services import mock_data

logger = logging.getLogger("app.agents")

NO_EVIDENCE_ANSWER = (
    "抱歉，未找到相关校规或校园服务信息。建议换一种说法重新提问，或联系辅导员 / 学生工作处进行人工确认。"
)
DEGRADE_NOTE = "\n\n（说明：本答案未通过引用校验，仅供参考，请以《学生手册》原文为准。）"

SYSTEM_PROMPT = (
    "你是高校『校园服务与校纪校规』智能问答助手。必须严格遵守：\n"
    "1. 只能依据【背景资料】作答，禁止编造条款编号、处分等级、办理流程；\n"
    "2. 若【背景资料】不足以回答问题，直接回答“抱歉，未找到相关校规或校园服务信息”，"
    "并建议咨询辅导员或学生工作处；\n"
    "3. 回答使用简体中文：先给结论，再逐条列出依据（注明文档名称与章节）；\n"
    "4. 语气客观严谨，不使用“应该”“大概”等模糊措辞。"
)
STRICT_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n5. 这是重新生成：上一版答案未通过引用校验，本轮必须逐条引用【背景资料】中的原文。"
)

TOOL_SYSTEM_PROMPT = (
    "你是校园服务智能体，具备工具调用能力。可用的工具：检索校规知识库、查询校园公告、"
    "创建转人工工单。\n"
    "规则：\n"
    "1. 涉及校规/办理流程/开放时间等问题，必须调用 search_rules 获取依据；\n"
    "2. 涉及最新通知/公告时调用 query_announcements；\n"
    "3. 用户明确要求投诉、报修、转人工时调用 create_ticket；\n"
    "4. 不需要工具时不要强行调用；不要编造工具参数。"
)


def _history_messages(state: AgentState, limit: int = 6) -> list[dict[str, str]]:
    history = state.history or []
    return [
        {"role": item["role"], "content": item["content"]}
        for item in history[-limit:]
        if item.get("role") in {"user", "assistant"} and item.get("content")
    ]


def build_messages(state: AgentState, strict: bool = False) -> list[dict[str, str]]:
    """组装证据约束 Prompt（含多轮历史）。"""
    blocks = [
        f"[{index}] {item.title} · {item.detail}（{item.type}，相似度 {item.score:.2f}）\n{item.snippet}"
        for index, item in enumerate(state.evidence, start=1)
    ]
    context = "\n\n".join(blocks) if blocks else "（无可用资料）"
    messages: list[dict[str, str]] = [
        {"role": "system", "content": STRICT_SYSTEM_PROMPT if strict else SYSTEM_PROMPT}
    ]
    messages.extend(_history_messages(state, limit=6))
    messages.append(
        {
            "role": "user",
            "content": (
                f"【背景资料】\n{context}\n\n"
                f"【用户问题】\n{state.rewritten_query or state.query}\n\n"
                "请严格依据【背景资料】回答，并在末尾注明引用条款。"
            ),
        }
    )
    return messages


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
# ②.1 多轮记忆 Agent（读取最近消息 + 追问改写）
# ---------------------------------------------------------------------------
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
    return {"history": history, "rewritten_query": rewritten}


# ---------------------------------------------------------------------------
# ②.2 Tool Calling Agent（LLM 自主选择工具 + ReAct 循环）
# ---------------------------------------------------------------------------
async def tool_agent(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """让 LLM 选择业务工具并执行；结果回填证据，或直接生成工具型回答。"""
    run_id = state.run_id or uuid.uuid4().hex[:12]
    runner = ToolRunner(ctx.services, ctx.settings)

    messages: list[dict[str, Any]] = [{"role": "system", "content": TOOL_SYSTEM_PROMPT}]
    messages.extend(_history_messages(state, limit=6))
    messages.append({"role": "user", "content": state.rewritten_query or state.query})

    results = []
    search_evidence: list[Evidence] = []
    tool_evidence: list[Evidence] = []
    call_records: list[dict[str, Any]] = []

    for _ in range(max(1, ctx.settings.max_tool_rounds)):
        try:
            assistant = await ctx.services.llm.chat_with_tools(messages, TOOL_DEFINITIONS)
        except Exception:  # noqa: BLE001 —— LLM/Tool 故障时回退到确定性 RAG
            logger.exception("Tool Calling LLM 调用失败，回退标准 RAG")
            break

        calls = assistant.get("tool_calls") or []
        if not calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": assistant.get("content") or "",
                "tool_calls": [
                    {
                        "id": call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": call.get("name") or "",
                            "arguments": call.get("arguments") or "{}",
                        },
                    }
                    for call in calls
                ],
            }
        )

        for call in calls:
            name = str(call.get("name") or "")
            call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:8]}")
            raw_arguments = call.get("arguments") or "{}"
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {}
            else:
                arguments = raw_arguments if isinstance(raw_arguments, dict) else {}

            await ctx.emit(
                "tool",
                {
                    "phase": "tool_start",
                    "node": "tool_agent",
                    "tool": name,
                    "arguments": arguments,
                },
            )
            result = await runner.run(
                name,
                arguments,
                user_id=state.user_id,
                conversation_id=state.conversation_id,
                run_id=run_id,
            )
            await ctx.emit(
                "tool",
                {
                    "phase": "tool_end",
                    "node": "tool_agent",
                    "tool": name,
                    "arguments": result.arguments,
                    "success": result.success,
                    "summary": result.summary[:800],
                    "latency_ms": result.latency_ms,
                },
            )
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": result.summary}
            )
            results.append(result)
            call_records.append(
                {
                    "name": name,
                    "arguments": result.arguments,
                    "success": result.success,
                    "summary": result.summary[:500],
                    "latency_ms": result.latency_ms,
                }
            )
            if name == "search_rules":
                search_evidence.extend(result.evidence)
            else:
                tool_evidence.extend(result.evidence)

    if not results:
        return {"run_id": run_id, "tool_calls": []}

    update: dict[str, Any] = {
        "run_id": run_id,
        "tool_calls": call_records,
        "tool_evidence": tool_evidence,
    }
    search_used = any(result.name == "search_rules" for result in results)
    if search_used:
        update.update(
            {
                "tool_search_used": True,
                "vector_hits": [item for item in search_evidence if item.type == "vector"],
                "graph_paths": [item for item in search_evidence if item.type == "graph"],
            }
        )
        return update

    # 只调用了公告/工单类工具：直接用工具结果生成回答，跳过 RAG 链路
    sources: list[SourceItem] = []
    for result in results:
        sources.extend(result.sources)
    evidence = [
        Evidence(
            type="tool",
            title=source.title,
            detail=source.detail,
            snippet=source.snippet,
            score=0.9,
        )
        for source in sources
    ]
    summaries = "\n\n".join(f"【{result.name}】{result.summary}" for result in results)
    answer_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                SYSTEM_PROMPT
                + "\n5. 本轮已真实调用业务工具，请依据【工具结果】回答，不要编造工具没有返回的信息。"
            ),
        }
    ]
    answer_messages.extend(_history_messages(state, limit=6))
    answer_messages.append(
        {
            "role": "user",
            "content": (
                f"【工具结果】\n{summaries}\n\n"
                f"【用户问题】\n{state.query}\n\n"
                "请简洁回答，并说明这些信息来自哪个工具。"
            ),
        }
    )
    pieces: list[str] = []
    async for piece in ctx.services.llm.stream(answer_messages):
        pieces.append(piece)
        await ctx.delta(piece)
    await ctx.emit("meta", {"sources": [source.model_dump() for source in sources], "cached": False})
    return {
        **update,
        "tool_only": True,
        "sources": sources,
        "evidence": evidence,
        "evidence_score": 0.9,
        "answer": "".join(pieces),
        "grounding_ok": True,
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
    for item in [*state.vector_hits, *state.graph_paths, *state.tool_evidence]:
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
ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条")
GRAPH_TITLE_PREFIX = "知识图谱 · "


async def verify_grounding(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    """引用校验：答案需引用证据的文档名或条款号。

    注意：
    - 图谱证据的标题带了"知识图谱 · "前缀，模型不会原样引用，匹配前要剥掉；
    - 只看标题太弱，补充"条款号"匹配（如 第6条），降低误判为降级的概率。
    """
    answer = state.answer or ""
    titles: list[str] = []
    article_numbers: list[str] = []
    for item in state.evidence:
        title = item.title or ""
        if title.startswith(GRAPH_TITLE_PREFIX):
            title = title[len(GRAPH_TITLE_PREFIX):]
        if title:
            titles.append(title)
        match = ARTICLE_RE.search(item.detail or "")
        if match:
            article_numbers.append(match.group(0))

    cited_by_title = any(title in answer for title in titles)
    cited_by_article = any(number in answer for number in article_numbers)
    ok = (cited_by_title or cited_by_article) and len(answer) >= 10
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
    # 只缓存"有引用校验通过"的正常答案：拒答、降级、缓存命中的结果都不写缓存
    if (
        not state.cached
        and not state.refused
        and not state.degraded
        and not state.tool_only
        and state.grounding_ok
        and state.answer
    ):
        ctx.services.cache.store(state.query, state.answer, state.sources)
    return {"conversation_id": conversation_id}
