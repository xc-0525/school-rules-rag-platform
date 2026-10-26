"""自研轻量 Agent 图执行器。

设计目标（课程答辩要求）：
1. 显式的"图"：节点 + 边 + 条件路由，而不是写死的函数调用；
2. 支持三种控制结构：并行（parallel）、选择（route）、循环（loop）；
3. 每个节点执行都产生 trace 事件，前端可实时画出"图在跑"；
4. 循环必须有硬上限（轮次计数 + 全局步数 + 单节点超时），防止跑飞。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agents.events import EventBus, NullBus
from app.agents.state import AgentState
from app.core.config import Settings

NodeRunFn = Callable[[AgentState, "NodeContext"], Awaitable[dict[str, Any]]]
"""节点函数：读 state，返回要更新的字段（dict）。"""

RouteFn = Callable[[AgentState], tuple[str | None, str]]
"""条件路由：返回（下一节点, 原因）。返回 None 表示结束。"""


class WorkflowError(RuntimeError):
    """工作流执行异常：节点缺失、超时、步数超限等。"""


@dataclass
class NodeSpec:
    """一个图节点（或一个并行分组）的声明。"""

    name: str
    label: str
    description: str = ""
    kind: Literal["agent", "parallel", "terminal"] = "agent"
    # 控制结构标记，供前端/文档展示：parallel=并行，selection=选择，loop=循环
    control: Literal["", "parallel", "selection", "loop"] = ""
    run: NodeRunFn | None = None
    children: list[str] = field(default_factory=list)  # kind=parallel 时的子节点
    next: str | None = None                            # 顺序边
    route: RouteFn | None = None                       # 条件边（优先于 next）


class NodeContext:
    """节点运行时上下文：依赖注入 + 事件推送。"""

    def __init__(self, services: Any, settings: Settings, bus: EventBus | NullBus) -> None:
        self.services = services
        self.settings = settings
        self.bus = bus

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        await self.bus.emit(event, payload)

    async def delta(self, content: str) -> None:
        """推送大模型生成的增量文本。"""
        await self.emit("delta", {"content": content})


class WorkflowEngine:
    """按 NodeSpec 图定义执行 Agent 工作流。"""

    def __init__(
        self,
        nodes: dict[str, NodeSpec],
        entry: str,
        services: Any,
        settings: Settings,
        bus: EventBus | NullBus | None = None,
    ) -> None:
        self.nodes = nodes
        self.entry = entry
        self.services = services
        self.settings = settings
        self.bus = bus or NullBus()
        self.events: list[dict[str, Any]] = []
        self.visited: list[str] = []
        self.started_at = 0.0

    # ------------------------------------------------------------------ 工具
    def _ms(self) -> int:
        if not self.started_at:
            return 0
        return int((time.perf_counter() - self.started_at) * 1000)

    def _elapsed(self, t0: float) -> int:
        return int((time.perf_counter() - t0) * 1000)

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        payload = {"ts_ms": self._ms(), **payload}
        self.events.append({"event": event, **payload})
        await self.bus.emit(event, payload)

    # ------------------------------------------------------------------ 执行
    async def run(self, state: AgentState) -> AgentState:
        """从 entry 节点开始跑图，直到走到没有下一节点为止。"""
        self.started_at = time.perf_counter()
        current: str | None = self.entry

        while current is not None:
            spec = self.nodes.get(current)
            if spec is None:
                raise WorkflowError(f"图定义错误：找不到节点 {current}")

            state.steps += 1
            if state.steps > self.settings.max_steps:
                raise WorkflowError(
                    f"工作流步数超过上限 {self.settings.max_steps}，疑似死循环（请检查路由）"
                )

            state.latency_ms = self._ms()
            update = await self._execute(spec, state)
            if update:
                state = state.model_copy(update=update)

            if spec.route is not None:
                nxt, reason = spec.route(state)
                await self._emit(
                    "trace",
                    {
                        "node": spec.name,
                        "phase": "route",
                        "from": spec.name,
                        "to": nxt,
                        "reason": reason,
                    },
                )
                current = nxt
            else:
                current = spec.next

        return state

    async def _execute(self, spec: NodeSpec, state: AgentState) -> dict[str, Any]:
        """执行节点；并行节点会 fan-out 到子节点后合并结果。"""
        if spec.kind == "parallel":
            t0 = time.perf_counter()
            self.visited.append(spec.name)
            await self._emit(
                "trace",
                {
                    "node": spec.name,
                    "phase": "start",
                    "kind": "parallel",
                    "control": "parallel",
                    "label": spec.label,
                },
            )
            # 并行执行所有子节点（控制结构①：并行）
            partials = await asyncio.gather(
                *(self._run_node(self.nodes[child], state) for child in spec.children)
            )
            merged: dict[str, Any] = {}
            for partial in partials:
                merged.update(partial or {})
            await self._emit(
                "trace",
                {
                    "node": spec.name,
                    "phase": "end",
                    "kind": "parallel",
                    "control": "parallel",
                    "latency_ms": self._elapsed(t0),
                },
            )
            return merged

        return await self._run_node(spec, state)

    async def _run_node(self, spec: NodeSpec, state: AgentState) -> dict[str, Any]:
        if spec.run is None:
            raise WorkflowError(f"节点 {spec.name} 没有实现 run()")

        t0 = time.perf_counter()
        self.visited.append(spec.name)
        await self._emit(
            "trace",
            {
                "node": spec.name,
                "phase": "start",
                "kind": spec.kind,
                "control": spec.control,
                "label": spec.label,
            },
        )
        ctx = NodeContext(self.services, self.settings, self.bus)
        try:
            update = await asyncio.wait_for(spec.run(state, ctx), timeout=self.settings.node_timeout)
        except asyncio.TimeoutError as exc:
            raise WorkflowError(f"节点「{spec.label or spec.name}」执行超时") from exc

        await self._emit(
            "trace",
            {
                "node": spec.name,
                "phase": "end",
                "kind": spec.kind,
                "latency_ms": self._elapsed(t0),
            },
        )
        return update or {}

    # ------------------------------------------------------------------ 信息
    def runtime_info(self, state: AgentState) -> dict[str, Any]:
        """给前端 / done 事件用的运行摘要。"""
        agent_nodes = [name for name in self.visited if self.nodes[name].kind == "agent"]
        return {
            "steps": state.steps,
            "visited": list(dict.fromkeys(self.visited)),
            "agent_node_runs": len(agent_nodes),
            "retrieval_round": state.retrieval_round,
            "regen_count": state.regen_count,
            "latency_ms": self._ms(),
        }

    def describe(self) -> dict[str, Any]:
        """输出图定义（用于 /api/v1/graph 与前端画图）。"""
        nodes = []
        for spec in self.nodes.values():
            nodes.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "description": spec.description,
                    "kind": spec.kind,
                    "control": spec.control,
                    "next": spec.next,
                    "children": spec.children,
                    "has_router": spec.route is not None,
                }
            )
        controls = sorted({spec.control for spec in self.nodes.values() if spec.control})
        return {
            "entry": self.entry,
            "node_count": len(self.nodes),
            "agent_node_count": sum(1 for spec in self.nodes.values() if spec.kind == "agent"),
            "control_structures": controls,
            "nodes": nodes,
        }
