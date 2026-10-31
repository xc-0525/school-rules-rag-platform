"""问答接口：/api/v1/chat（SSE 流式 + Agent 轨迹）与 /api/v1/graph（图定义）。

SSE 事件协议：
- meta  证据来源（融合后 / 缓存命中）
- trace 节点轨迹：{node, phase: start/end/route, kind, control, latency_ms, from, to, reason}
- delta 大模型逐字增量
- done  完整答案 + 运行摘要（步数、访问节点、循环轮次）
- error 终止事件
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.events import EventBus
from app.agents.runtime import WorkflowEngine, WorkflowError
from app.agents.state import AgentState
from app.core.config import Settings
from app.schemas.chat import ChatRequest, ChatResponse, SourceItem
from app.services.auth import AuthUser, build_current_user_dependency
from app.services.llm import LLMError


def sse_event(event: str, payload: dict[str, Any]) -> str:
    """按 SSE 规范编码一条事件。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, (LLMError, WorkflowError)):
        return str(exc)
    return f"服务内部错误：{type(exc).__name__}: {exc}"


def _source_dicts(state: AgentState) -> list[dict[str, Any]]:
    return [source.model_dump() for source in state.sources]


def _done_payload(state: AgentState, engine: WorkflowEngine, started: float) -> dict[str, Any]:
    return {
        "answer": state.answer,
        "sources": _source_dicts(state),
        "refused": state.refused,
        "degraded": state.degraded,
        "cached": state.cached,
        "conversation_id": state.conversation_id,
        "intent": state.intent,
        "retrieval_round": state.retrieval_round,
        "regen_count": state.regen_count,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "runtime": engine.runtime_info(state),
    }


def build_router(
    services: Any,
    settings: Settings,
    nodes: dict[str, Any],
    entry: str,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["问答"])
    current_user = build_current_user_dependency(settings)

    def make_engine(bus: Any = None) -> WorkflowEngine:
        return WorkflowEngine(nodes, entry, services, settings, bus)

    @router.post(
        "/chat",
        response_model=ChatResponse,
        responses={
            200: {
                "content": {
                    "text/event-stream": {
                        "schema": {"type": "string"},
                        "example": (
                            "event: trace\ndata: {\"node\": \"retrieve_vector\", \"phase\": \"start\"}\n\n"
                            "event: delta\ndata: {\"content\": \"根据\"}\n\n"
                            "event: done\ndata: {\"answer\": \"...\", \"runtime\": {\"steps\": 10}}\n\n"
                        ),
                    }
                },
                "description": "stream=true 返回 SSE（含 Agent 轨迹）；stream=false 返回 ChatResponse JSON。",
            }
        },
        summary="多智能体问答（真实 DeepSeek + SSE 流式 + Agent 轨迹）",
    )
    async def chat(request: ChatRequest, user: AuthUser | None = Depends(current_user)):
        state = AgentState(
            query=request.query,
            conversation_id=request.conversation_id,
            user_id=user.id if user else 1,
            run_id=uuid.uuid4().hex[:12],
        )

        # ---------------- 非流式：一次性 JSON（Swagger 调试方便） ----------------
        if not request.stream:
            engine = make_engine()
            try:
                final = await engine.run(state)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
            return ChatResponse(
                query=final.query,
                answer=final.answer,
                sources=[SourceItem(**source) for source in _source_dicts(final)],
                refused=final.refused,
                degraded=final.degraded,
                cached=final.cached,
                conversation_id=final.conversation_id,
                runtime=engine.runtime_info(final),
            )

        # ---------------- 流式：后台跑图，事件经 EventBus 推给 SSE ----------------
        bus = EventBus()
        engine = make_engine(bus)
        started = time.perf_counter()

        async def runner() -> None:
            try:
                final = await engine.run(state)
                await bus.emit("done", _done_payload(final, engine, started))
            except Exception as exc:  # noqa: BLE001
                await bus.emit("error", {"message": _friendly_error(exc)})
            finally:
                await bus.close()

        task = asyncio.create_task(runner())

        async def event_stream():
            while True:
                item = await bus.get()
                if item is None:
                    break
                event, payload = item
                yield sse_event(event, payload)
            await task  # 让后台任务里的异常在响应结束时暴露（runner 已自行兜底）

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/graph", summary="获取 Agent 图定义（节点 / 边 / 控制结构）")
    async def get_graph() -> dict[str, Any]:
        engine = make_engine()
        description = engine.describe()
        description["modes"] = {
            "vector": settings.vector_mode,
            "graph": settings.graph_mode,
            "llm": settings.llm_mode,
        }
        description["limits"] = {
            "max_steps": settings.max_steps,
            "max_retrieval_rounds": settings.max_retrieval_rounds,
            "max_regen": settings.max_regen,
            "evidence_threshold": settings.evidence_threshold,
        }
        return description

    return router
