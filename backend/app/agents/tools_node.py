"""Tool Calling 节点：LLM 自主选工具 → ReAct 循环 → 工具型直答。

从 nodes.py 抽出（原 188 行，占整个文件近三成），它是"工具编排"而不是"图上节点"。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from app.agents.prompting import _history_messages, _prompt_question
from app.agents.prompts import SYSTEM_PROMPT, current_date_line, tool_system_prompt
from app.agents.fusion import _evidence_key
from app.agents.runtime import NodeContext
from app.agents.state import AgentState
from app.agents.tools import ToolRunner, active_tool_definitions
from app.schemas.chat import Evidence, SourceItem

logger = logging.getLogger("app.agents")

async def tool_agent(state: AgentState, ctx: NodeContext) -> dict[str, Any]:
    """让 LLM 选择业务工具并执行；结果回填证据，或直接生成工具型回答。"""
    run_id = state.run_id or uuid.uuid4().hex[:12]
    runner = ToolRunner(ctx.services, ctx.settings)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": tool_system_prompt(ctx.settings) + "\n" + current_date_line()}
    ]
    messages.extend(_history_messages(state, limit=6))
    messages.append({"role": "user", "content": _prompt_question(state)})

    results = []
    search_evidence: list[Evidence] = []
    tool_evidence: list[Evidence] = []
    call_records: list[dict[str, Any]] = []

    for _ in range(max(1, ctx.settings.max_tool_rounds)):
        try:
            assistant = await ctx.services.llm.chat_with_tools(
                messages, active_tool_definitions(ctx.settings)
            )
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
                current_date_line()
                + "\n\n"
                + SYSTEM_PROMPT
                + "\n5. 本轮已真实调用业务工具，请依据【工具结果】回答，不要编造工具没有返回的信息。\n"
                "6. 若工具结果是场馆余量：列出可预约的时段，并提示“告诉我要约的位置和时段，"
                "我可以生成预约草稿，确认后才会占用”——本系统支持预约（本规则优先于第 2 条），"
                "禁止回答“无法代为预约”“请咨询辅导员/学生工作处”这类话术。\n"
                "7. 若工具结果是草稿（预约/失物招领/报修/取消预约/失物结案）：说明这是待确认草稿，"
                "请用户点击下方对应确认按钮，禁止说“已预约/已发布/已提交/已取消成功”；\n"
                "   本轮没有生成任何草稿时，禁止声称“有待确认草稿”——那会让用户找不到确认按钮；"
                "用户仍在表达取消/预约/发布等意图时，必须重新调用对应工具生成新草稿，"
                "不要只复述“之前已有草稿”或让用户回去找早前消息里的按钮。\n"
                "8. 若工具结果是“我的失物招领/我的预约/我的报修”：逐条列出编号与状态，"
                "需要结案或取消时提示可以直接说要取消哪一条。"
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
    # 工具可能被 LLM 连续调用多次（ReAct 两轮），同一公告会被重复收录。
    # tool-only 分支不经过 fuse_evidence，去重必须在这里做。
    unique_evidence: list[Evidence] = []
    unique_sources: list[SourceItem] = []
    seen_evidence: set[tuple[str, str, str]] = set()
    seen_sources: set[tuple[str, str, str]] = set()
    for item in evidence:
        key = _evidence_key(item)
        if key not in seen_evidence:
            seen_evidence.add(key)
            unique_evidence.append(item)
    for source in sources:
        key = (source.type, re.sub(r"\s+", "", source.snippet or "")[:120], "")
        if key not in seen_sources:
            seen_sources.add(key)
            unique_sources.append(source)

    await ctx.emit(
        "meta", {"sources": [source.model_dump() for source in unique_sources], "cached": False}
    )
    return {
        **update,
        "tool_only": True,
        "sources": unique_sources,
        "evidence": unique_evidence,
        "evidence_score": 0.9,
        "answer": "".join(pieces),
        # P0-4：工具直答没有走引用校验，必须标 skipped 而不是伪造成 verified
        "grounding_status": "skipped",
        "grounding_reason": "工具结果直答，未经过条款引用校验",
    }
