"""Tool Calling 与多轮记忆的回归测试。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.graph import build_graph  # noqa: E402
from app.agents.runtime import WorkflowEngine  # noqa: E402
from app.agents.state import AgentState  # noqa: E402
from app.agents.tools import TOOL_DEFINITIONS, ToolRunner  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import DEFAULT_FAKE_ANSWER, FakeLLM  # noqa: E402


def make_settings() -> Settings:
    return Settings(llm_mode="fake", vector_mode="mock", graph_mode="mock")


def build_services_for_test():
    settings = make_settings()
    services = build_services(settings)
    services.llm = FakeLLM()
    return settings, services


class ToolCallingFakeLLM(FakeLLM):
    """第一轮返回指定工具调用，第二轮不再调用（模拟 DeepSeek function calling）。"""

    def __init__(self, tool_name: str, arguments: str = "{}") -> None:
        super().__init__(DEFAULT_FAKE_ANSWER)
        self.tool_name = tool_name
        self.arguments = arguments
        self.rounds = 0

    async def chat_with_tools(self, messages, tools):  # noqa: ARG002
        self.rounds += 1
        if self.rounds == 1:
            return {
                "content": "",
                "tool_calls": [
                    {"id": "call_test_1", "name": self.tool_name, "arguments": self.arguments}
                ],
            }
        return {"content": "", "tool_calls": []}


class ToolRunnerTests(unittest.TestCase):
    def test_tool_definitions(self) -> None:
        names = {item["function"]["name"] for item in TOOL_DEFINITIONS}
        self.assertEqual(
            names,
            {"search_rules", "query_announcements", "create_ticket"},
        )

    def test_search_rules_returns_evidence(self) -> None:
        settings, services = build_services_for_test()
        runner = ToolRunner(services, settings)
        result = asyncio.run(
            runner.run(
                "search_rules",
                {"query": "宿舍晚上几点熄灯", "top_k": 3},
                user_id=1,
                conversation_id=None,
                run_id="test-run",
            )
        )
        self.assertTrue(result.success)
        self.assertGreater(len(result.evidence), 0)
        self.assertGreater(len(result.sources), 0)
        self.assertIn("检索到", result.summary)

    def test_query_announcements_mock_fallback(self) -> None:
        settings, services = build_services_for_test()
        runner = ToolRunner(services, settings)
        result = asyncio.run(
            runner.run(
                "query_announcements",
                {"keyword": "图书馆", "limit": 5},
                user_id=1,
                conversation_id=None,
                run_id="test-run",
            )
        )
        self.assertTrue(result.success)
        self.assertIn("图书馆", result.summary)
        self.assertTrue(any(source.type == "tool" for source in result.sources))

    def test_create_ticket_mock_mode(self) -> None:
        settings, services = build_services_for_test()
        runner = ToolRunner(services, settings)
        result = asyncio.run(
            runner.run(
                "create_ticket",
                {
                    "title": "宿舍噪音投诉",
                    "description": "3 号楼 402 夜间噪音较大",
                    "category": "宿舍",
                },
                user_id=1,
                conversation_id=None,
                run_id="test-run",
            )
        )
        self.assertTrue(result.success)
        self.assertRegex(result.payload["ticket_no"], r"^T\d{8}[A-Z0-9]+$")
        self.assertIn("工单", result.summary)


class ToolCallingGraphTests(unittest.TestCase):
    def test_tool_only_answer_skips_rag(self) -> None:
        settings = make_settings()
        services = build_services(settings)
        services.llm = ToolCallingFakeLLM(
            "query_announcements", '{"keyword": "图书馆"}'
        )
        nodes_spec, entry = build_graph(settings)
        engine = WorkflowEngine(nodes_spec, entry, services, settings)
        state = asyncio.run(engine.run(AgentState(query="最近图书馆有什么公告？")))

        self.assertTrue(state.tool_only)
        self.assertTrue(state.answer)
        self.assertGreaterEqual(len(state.sources), 1)
        self.assertTrue(any(source.type == "tool" for source in state.sources))
        self.assertIn("tool_agent", engine.visited)
        self.assertIn("persist_answer", engine.visited)
        self.assertNotIn("parallel_retrieval", engine.visited)
        self.assertTrue(any(event.get("event") == "tool" for event in engine.events))

    def test_search_tool_feeds_evidence_fusion(self) -> None:
        settings = make_settings()
        services = build_services(settings)
        services.llm = ToolCallingFakeLLM(
            "search_rules", '{"query": "宿舍晚上几点熄灯", "top_k": 3}'
        )
        nodes_spec, entry = build_graph(settings)
        engine = WorkflowEngine(nodes_spec, entry, services, settings)
        state = asyncio.run(engine.run(AgentState(query="宿舍晚上几点熄灯？")))

        self.assertTrue(state.tool_search_used)
        self.assertGreater(len(state.vector_hits), 0)
        self.assertIn("fuse_evidence", engine.visited)
        self.assertIn("generate_answer", engine.visited)
        self.assertNotIn("parallel_retrieval", engine.visited)
        self.assertGreaterEqual(len(state.tool_calls), 1)
        self.assertTrue(state.answer)


class MemoryTests(unittest.TestCase):
    def test_recent_messages_and_prompt_history(self) -> None:
        settings, services = build_services_for_test()

        async def scenario():
            first = AgentState(query="宿舍晚上几点熄灯？", user_id=1)
            nodes_spec, entry = build_graph(settings)
            engine = WorkflowEngine(nodes_spec, entry, services, settings)
            final = await engine.run(first)
            self.assertIsNotNone(final.conversation_id)

            history = await services.persistence.recent_messages(
                final.conversation_id, 1, limit=6
            )
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0]["role"], "user")
            self.assertEqual(history[1]["role"], "assistant")

            # 第二轮带上 conversation_id：memory_agent 应该把历史注入 state.history
            second = AgentState(
                query="那第二条呢？",
                conversation_id=final.conversation_id,
                user_id=1,
            )
            engine2 = WorkflowEngine(nodes_spec, entry, services, settings)
            final2 = await engine2.run(second)
            self.assertGreaterEqual(len(final2.history), 2)
            self.assertIn("memory_agent", engine2.visited)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
