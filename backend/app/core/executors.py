"""专用线程池：把"重活"与默认线程池隔离。

默认 `asyncio.to_thread` 用的是事件循环的共享线程池（约 min(32, CPU+4) 个），
数据库落库、BGE 编码、整条入库流水线都挤在里面。上传一份大 PDF 时
（解析 + 向量化 + 写三库，可能几分钟）会把线程全占满，表现为"上传时全站变慢"。

P2-2：入库单独一个单线程池，串行执行（本地嵌入式 Qdrant 本来也不支持并发写）。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

INGEST_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")


async def run_in_ingest_pool(func, /, *args):
    """在入库专用线程池里执行同步函数，避免占用事件循环默认线程池。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(INGEST_EXECUTOR, func, *args)


def shutdown_ingest_pool() -> None:
    INGEST_EXECUTOR.shutdown(wait=False, cancel_futures=True)
