"""持久化适配器：问答记录落库。

Day1 先用 Mock（打印）；Day2 换成 MySQL（SQLAlchemy），接口不变。
"""

from __future__ import annotations

from typing import Protocol

from app.agents.state import AgentState


class Persistence(Protocol):
    async def save_qa(self, state: AgentState) -> None: ...


class MockPersistence:
    """Mock 落库：在终端打印，方便演示时观察。"""

    async def save_qa(self, state: AgentState) -> None:
        preview = state.answer.replace("\n", " ")[:60]
        flags = []
        if state.cached:
            flags.append("cached")
        if state.refused:
            flags.append("refused")
        if state.degraded:
            flags.append("degraded")
        flag_text = f" [{'/'.join(flags)}]" if flags else ""
        print(
            f"[MySQL-Mock] 已保存问答{flag_text}：{preview}"
            f"（query={state.query[:30]}，循环轮次={state.retrieval_round}，重生成={state.regen_count}）"
        )


class MySQLPersistence:
    """MySQL 落库。Day2 实现（conversations / messages 两张表）。"""

    def __init__(self, url: str = "") -> None:
        self.url = url

    async def save_qa(self, state: AgentState) -> None:  # noqa: ARG002
        raise NotImplementedError("MySQL 持久化将在 Day2 接入")
