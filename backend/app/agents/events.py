"""SSE 事件总线。

图引擎里的节点（包括并行节点）通过 EventBus 把事件写进队列，
API 层从队列里取出并转成 SSE。这样并行分支也能安全地把事件推给同一个响应流。
"""

from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    """基于 asyncio.Queue 的事件总线，close() 后消费者退出。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        await self._queue.put((event, payload))

    async def close(self) -> None:
        await self._queue.put(None)

    async def get(self) -> tuple[str, dict[str, Any]] | None:
        return await self._queue.get()


class NullBus:
    """非流式场景用的空总线：事件照常产生但不推送。"""

    async def emit(self, event: str, payload: dict[str, Any]) -> None:  # noqa: ARG002
        return None

    async def close(self) -> None:
        return None

    async def get(self):  # pragma: no cover - 仅满足类型
        return None
