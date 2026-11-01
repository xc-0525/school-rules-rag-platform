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
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.events import EventBus
from app.agents.runtime import WorkflowEngine, WorkflowError
from app.agents.state import AgentState
from app.core.config import Settings
from app.core.request_context import current_request_id
from app.schemas.chat import ChatRequest, ChatResponse, SourceItem
from app.services.auth import AuthUser, build_current_user_dependency
from app.services.llm import LLMError


logger = logging.getLogger("app.chat")

FALLBACK_ERROR_ANSWER = (
    "抱歉，本次问答在处理过程中出现异常，未能生成答案。提问记录已保存，可稍后重试。"
)


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
        "grounding_status": state.grounding_status,
        "grounding_reason": state.grounding_reason,
        "llm_failed": state.llm_failed,
        "request_id": current_request_id(),
        "conversation_id": state.conversation_id,
        "message_id": state.message_id,
        "tool_drafts": state.tool_drafts,
        "intent": state.intent,
        "expansion_terms": state.expansion_terms,
        "evidence_meta": state.evidence_meta,
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
    limiter: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["问答"])
    current_user = build_current_user_dependency(settings)

    def _rate_key(user: AuthUser) -> str:
        return f"u:{user.id}"

    def _guard_daily(user: AuthUser) -> str:
        """日配额检查；返回限流键。未启用限流时只返回键。"""
        key = _rate_key(user)
        if limiter is not None and settings.rate_limit_enabled:
            allowed, _ = limiter.hit_daily(key)
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"今日问答次数已达上限（{settings.rate_limit_per_day} 次），请明天再试",
                )
        return key

    def _acquire(key: str) -> None:
        if limiter is not None and settings.rate_limit_enabled and not limiter.acquire(key):
            raise HTTPException(
                status_code=429,
                detail="已有问答正在进行，请等上一条回答结束后再提问",
                headers={"Retry-After": "1"},
            )

    def _release(key: str) -> None:
        if limiter is not None and settings.rate_limit_enabled:
            limiter.release(key)

    def make_engine(bus: Any = None) -> WorkflowEngine:
        return WorkflowEngine(nodes, entry, services, settings, bus)

    async def persist_failure(state: AgentState) -> None:
        """兜底：图执行抛异常时也要留下一条记录。

        否则用户只看到 502/报错，会话历史里什么都没有——这正是"未配置 API Key
        时记录全部丢失"的原因。已经落过库（conversation_id 已回填）则跳过。
        """
        if state.conversation_id:
            return
        snapshot = state.model_copy(
            update={
                "answer": FALLBACK_ERROR_ANSWER,
                "degraded": True,
                "grounding_status": "skipped",
                "sources": [],
            }
        )
        try:
            await services.persistence.save_qa(snapshot)
        except Exception:  # noqa: BLE001 —— 兜底失败不能再抛，否则连报错都返回不去
            logger.exception("兜底落库失败 user_id=%s", state.user_id)

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
    async def chat(request: ChatRequest, user: AuthUser = Depends(current_user)):
        # current_user 要么返回真实用户，要么返回独立的 anonymous 账号，不会返回 None；
        # 因此这里不存在"匿名回落 user_id=1（管理员）"的越权路径。
        state = AgentState(
            query=request.query,
            conversation_id=request.conversation_id,
            user_id=user.id,
            run_id=uuid.uuid4().hex[:12],
        )

        rate_key = _guard_daily(user)

        # ---------------- 非流式：一次性 JSON（Swagger 调试方便） ----------------
        if not request.stream:
            _acquire(rate_key)
            engine = make_engine()
            try:
                final = await engine.run(state)
            except Exception as exc:  # noqa: BLE001
                await persist_failure(engine.state or state)
                raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
            finally:
                _release(rate_key)
            return ChatResponse(
                query=final.query,
                answer=final.answer,
                sources=[SourceItem(**source) for source in _source_dicts(final)],
                refused=final.refused,
                degraded=final.degraded,
                cached=final.cached,
                grounding_status=final.grounding_status,
                grounding_reason=final.grounding_reason,
                llm_failed=final.llm_failed,
                request_id=current_request_id(),
                conversation_id=final.conversation_id,
                message_id=final.message_id,
                tool_drafts=final.tool_drafts,
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
                await persist_failure(engine.state or state)
                await bus.emit("error", {"message": _friendly_error(exc)})
            finally:
                await bus.close()

        _acquire(rate_key)
        try:
            task = asyncio.create_task(runner())
        except Exception:
            _release(rate_key)
            raise

        async def event_stream():
            try:
                while True:
                    item = await bus.get()
                    if item is None:
                        break
                    event, payload = item
                    yield sse_event(event, payload)
                await task  # 让后台任务里的异常在响应结束时暴露（runner 已自行兜底）
            finally:
                # P2-4：客户端中途断开（关闭页面/切网）时，取消后台图执行，
                # 否则会继续调用大模型、继续写库，白烧 token。
                if not task.done():
                    task.cancel()
                _release(rate_key)

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
