"""请求级 request_id（trace_id）。

目的：一次问答会跨 10+ 个节点、4~5 个模块打日志，没有统一 id 时排查只能靠时间戳猜。
这里用 contextvar 把 id 绑到"当前请求"，日志 filter 自动注入，响应头回显，
前端把它带进 done 事件——出问题时用户报一个 id 就能定位整条链路。

实现注意：
- 中间件里**不做 reset**：SSE 的生成器在 `call_next` 返回之后才继续执行，
  reset 会让流式阶段的日志丢掉 id。每个请求本来就有独立的 context 副本，
  不 reset 不会污染其他请求。
- 请求方可以自带 `X-Request-ID`（网关/前端重试链路），没有就生成。
"""

from __future__ import annotations

import contextvars
import logging
import uuid

from fastapi import FastAPI, Request

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def current_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> None:
    """显式设置当前上下文的 request_id（测试或后台任务续用父任务 id 时用）。"""
    _request_id.set(value)


class RequestIdFilter(logging.Filter):
    """给每条日志记录补上 request_id 字段，供 formatter 使用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def install_request_id(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = (request.headers.get(REQUEST_ID_HEADER) or "").strip() or uuid.uuid4().hex[:12]
        _request_id.set(rid)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
