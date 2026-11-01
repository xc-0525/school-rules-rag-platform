"""Agent 图中的节点实现。

每个节点是 `async def (state, ctx) -> dict`：
- 只返回要修改的状态字段，由图引擎合并；
- 需要流式输出时通过 `ctx.delta()` 推送；
- 选择/循环的控制逻辑放在 `graph.py` 的路由函数里，节点本身保持纯粹。

本文件在审查整改中承担了四个 P0/P1 修复：
P0-5  查询理解/扩展：拆分 generation_query 与 retrieval_query，扩展改为查同义词表；
P0-4  引用校验：三态 grounding_status + 条款号必须落在召回集内；
P1-3  证据融合：RRF 排序 + 分路可比的 evidence_score；
P1-5  重新生成前推送 reset，避免前端把两版答案拼在一起。
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
from app.services import lexicon

logger = logging.getLogger("app.agents")

NO_EVIDENCE_ANSWER = (
    "抱歉，未找到相关校规或校园服务信息。建议换一种说法重新提问，或联系辅导员 / 学生工作处进行人工确认。"
)
DEGRADE_NOTE = "\n\n（说明：本答案未通过引用校验，仅供参考，请以《学生手册》原文为准。）"
LLM_UNAVAILABLE_ANSWER = (
    "抱歉，大模型服务当前不可用（未配置 DEEPSEEK_API_KEY，或调用失败），"
    "本次只完成了知识库检索、未能生成答案。你的提问记录已保存，可稍后重试。"
)

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
    + "\n5. 这是重新生成：上一版答案未通过引用校验，本轮必须逐条引用【背景资料】中的原文，"
    "且条款号必须来自【背景资料】，不得出现资料中没有的条款编号。"
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


def _prompt_question(state: AgentState) -> str:
    """给大模型看的问题：只认自然问句，绝不使用被同义词污染的检索串。"""
    return state.generation_query or state.query


def _search_text(state: AgentState) -> str:
    """给检索用的串：优先使用扩展后的 retrieval_query。"""
    return state.retrieval_query or state.generation_query or state.query


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
                f"【用户问题】\n{_prompt_question(state)}\n\n"
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
    await ctx.delta(answer)
    return {"cached": True, "answer": answer, "sources": sources}


# ---------------------------------------------------------------------------
# ② 查询理解 Agent
# ---------------------------------------------------------------------------
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
    # 改写结果同时作为生成与检索的起点；扩展只允许改 retrieval_query
    return {"history": history, "generation_query": rewritten, "retrieval_query": rewritten}


# ---------------------------------------------------------------------------
# ②.2 Tool Calling Agent（LLM 自主选择工具 + ReAct 循环）
# ---------------------------------------------------------------------------
async def tool_agent(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """让 LLM 选择业务工具并执行；结果回填证据，或直接生成工具型回答。"""
    run_id = state.run_id or uuid.uuid4().hex[:12]
    runner = ToolRunner(ctx.services, ctx.settings)

    messages: list[dict[str, Any]] = [{"role": "system", "content": TOOL_SYSTEM_PROMPT}]
    messages.extend(_history_messages(state, limit=6))
    messages.append({"role": "user", "content": _prompt_question(state)})

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
                # 工单类写操作要用"用户原话"判断意图，不能用检索扩展串
                query=_prompt_question(state),
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

    # 未落库的工单草稿：前端据此渲染"确认提交"按钮。
    # 按内容去重：模型在 ReAct 第二轮可能重复调同一个工具，不去重会出两张一样的草稿。
    drafts: list[dict[str, Any]] = []
    seen_drafts: set[tuple[Any, ...]] = set()
    for result in results:
        payload = result.payload
        if not payload.get("requires_confirmation"):
            continue
        key = (payload.get("title"), payload.get("description"), payload.get("category"))
        if key in seen_drafts:
            continue
        seen_drafts.add(key)
        drafts.append(payload)

    update: dict[str, Any] = {
        "run_id": run_id,
        "tool_calls": call_records,
        "tool_evidence": tool_evidence,
        "tool_drafts": drafts,
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
                f"【用户问题】\n{_prompt_question(state)}\n\n"
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
        # P0-4：工具直答没有走引用校验，必须标 skipped 而不是伪造成 verified
        "grounding_status": "skipped",
        "grounding_reason": "工具结果直答，未经过条款引用校验",
    }


# ---------------------------------------------------------------------------
# ③④ 并行召回：检索 Agent（Qdrant）/ 图谱 Agent（Neo4j）
# ---------------------------------------------------------------------------
async def retrieve_vector(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    hits = await ctx.services.vector.search(_search_text(state), top_k=3)
    return {"vector_hits": hits}


async def retrieve_graph(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    paths = await ctx.services.graph.search_paths(_search_text(state), top_k=2)
    return {"graph_paths": paths}


# ---------------------------------------------------------------------------
# ⑤ 证据融合 Agent
# ---------------------------------------------------------------------------
def _evidence_key(item: Evidence) -> tuple[str, str, str]:
    return (item.type, item.title, item.detail)


async def fuse_evidence(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """RRF 融合排序 + 分路标定的证据分。

    P1-3 修复：
    - 旧实现按 `item.score` 直接排序，而图谱分是 `0.55 + 0.1*hits` 的硬编码，
      必然高于向量 cosine，导致"只要图谱命中就判证据充分"；
    - 现在排序用 RRF（只看排名，不看原始量纲），充分性判定仍用 0~1 的标定分，
      避免把阈值语义改掉（RRF 原始分只有 0.03 量级，直接比 0.5 会导致全站拒答）。
    """
    k = max(1, int(ctx.settings.rrf_k))

    # ---- 图谱质量闸门 ----
    # 图谱一路的分数是按关键词覆盖率标定的，覆盖率低时分会接近 0.46~0.5。
    # 这种"弱命中"如果照样参与融合，会挤占排序位置、稀释向量召回的好结果
    # （评测里表现为双路 MRR 反而低于仅向量）。因此 gated 模式下：
    # 图谱最高分不到准入线 → 整路不参与，并在 evidence_meta 留痕。
    graph_items = list(state.graph_paths)
    graph_best = max((item.score for item in graph_items), default=0.0)
    graph_gated = False
    if ctx.settings.fusion_mode == "gated" and graph_items:
        if graph_best < ctx.settings.graph_quality_min_score:
            graph_gated = True
            graph_items = []

    lists = [state.vector_hits, graph_items, state.tool_evidence]

    rrf: dict[tuple[str, str, str], float] = {}
    merged_map: dict[tuple[str, str, str], Evidence] = {}
    for items in lists:
        seen_in_list: set[tuple[str, str, str]] = set()
        for rank, item in enumerate(items, start=1):
            key = _evidence_key(item)
            if key in seen_in_list:  # 同一路里的重复不计分
                continue
            seen_in_list.add(key)
            rrf[key] = rrf.get(key, 0.0) + 1.0 / (k + rank)
            current = merged_map.get(key)
            if current is None or item.score > current.score:
                merged_map[key] = item

    merged = sorted(
        merged_map.values(),
        key=lambda item: (rrf.get(_evidence_key(item), 0.0), item.score),
        reverse=True,
    )

    # ---- 图谱条目占比上限 ----
    # 即使图谱质量达标，也不允许它占满 prompt：按 graph_max_share 截断，
    # 把位置留给向量召回（条款级召回通常更贴题）。
    graph_kept = sum(1 for item in merged if item.type == "graph")
    if merged and ctx.settings.graph_max_share > 0:
        cap = max(1, int(len(merged) * ctx.settings.graph_max_share + 0.999))
        kept: list[Evidence] = []
        graph_count = 0
        for item in merged:
            if item.type == "graph":
                if graph_count >= cap:
                    continue
                graph_count += 1
            kept.append(item)
        merged = kept
        graph_kept = graph_count

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
    meta = {
        "fusion_mode": ctx.settings.fusion_mode,
        "rrf_k": k,
        "rrf_top": round(max(rrf.values(), default=0.0), 4),
        "vector_max": round(max((item.score for item in state.vector_hits), default=0.0), 3),
        "graph_max": round(graph_best, 3),
        "graph_count": len(state.graph_paths),
        "graph_kept": graph_kept,
        "graph_gated": graph_gated,
        "tool_count": len(state.tool_evidence),
    }
    await ctx.emit(
        "meta",
        {"sources": [source.model_dump() for source in sources], "cached": False, "fusion": meta},
    )
    return {
        "evidence": merged,
        "evidence_score": score,
        "evidence_meta": meta,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# ⑥ 充分性判定（选择结构：充分 / 扩展重试 / 拒答）
# ---------------------------------------------------------------------------
async def check_sufficiency(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    # 判定逻辑本身在 graph.route_sufficiency 里；保留节点是为了在 SSE 轨迹上
    # 显式展示"判定"这一步（论文里可写：路由决策在 route 函数，节点用于轨迹标记）。
    return {}


# ---------------------------------------------------------------------------
# ⑦ 查询扩展（循环结构：回到并行召回）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# ⑧ 生成 Agent（DeepSeek 流式，证据约束）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# ⑨ 引用校验 Agent（选择 + 循环：不通过则重新生成）
# ---------------------------------------------------------------------------
ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百零〇0-9]+条")
GRAPH_TITLE_PREFIX = "知识图谱 · "


def _clean_title(title: str) -> str:
    title = (title or "").strip()
    if title.startswith(GRAPH_TITLE_PREFIX):
        title = title[len(GRAPH_TITLE_PREFIX):]
    return title.strip()


def _collect_evidence_citations(evidence: list[Evidence]) -> tuple[set[str], set[str]]:
    """从召回证据里抽出可核对的两类标识：文档名、条款号。"""
    titles: set[str] = set()
    articles: set[str] = set()
    for item in evidence:
        title = _clean_title(item.title)
        if title:
            titles.add(title)
        articles.update(ARTICLE_RE.findall(item.detail or ""))
    if not articles:
        # 详情里没有条款号时（工具证据/公告），退回从原文里抽，避免一律判失败
        for item in evidence:
            articles.update(ARTICLE_RE.findall(item.snippet or ""))
    return titles, articles


async def verify_grounding(state: AgentState, ctx: NodeContext) -> dict[str, Any]:  # noqa: ARG001
    """引用校验（三态）。

    判定规则（比旧版严格）：
    1. 答案里出现的**每一个**条款号都必须能在召回证据里找到 —— 编造"第99条"直接不通过；
    2. 至少要引用一个条款号或一个文档名 —— 通篇不带依据也不通过；
    3. 答案长度下限 10 字。

    仍然做不到"claim 级蕴含判断"（那需要 NLI 模型），但已经堵住最典型的两种
    幻觉：编造条款号、完全不引用。论文里应如实描述为"引用可核对性校验"。
    """
    answer = state.answer or ""
    titles, articles = _collect_evidence_citations(state.evidence)
    cited = set(ARTICLE_RE.findall(answer))
    fabricated = sorted(cited - articles)
    title_hit = any(title in answer for title in titles if title)
    unsupported = sorted(cited) if not articles and cited else fabricated

    if len(answer.strip()) < 10:
        return {
            "grounding_status": "unverified",
            "grounding_reason": "答案过短，无法核对引用",
            "regen_count": state.regen_count + 1,
        }
    if unsupported:
        return {
            "grounding_status": "unverified",
            "grounding_reason": "引用了未召回的条款：" + "、".join(unsupported[:3]),
            "regen_count": state.regen_count + 1,
        }
    if not cited and not title_hit:
        return {
            "grounding_status": "unverified",
            "grounding_reason": "未引用任何条款号或文档名",
            "regen_count": state.regen_count + 1,
        }
    return {
        "grounding_status": "verified",
        "grounding_reason": (
            f"引用条款 {len(cited)} 处，均在召回集内" if cited else "引用了召回的文档名"
        ),
    }


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
