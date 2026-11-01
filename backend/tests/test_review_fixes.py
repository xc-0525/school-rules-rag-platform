"""审查整改回归测试（P0-1 / P0-4 / P0-5 / P1-3 / P1-7 / P1-8）。

运行：cd backend && python -m unittest tests.test_review_fixes -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import nodes  # noqa: E402
from app.agents.events import NullBus  # noqa: E402
from app.agents.graph import build_graph  # noqa: E402
from app.agents.runtime import NodeContext, WorkflowEngine  # noqa: E402
from app.agents.state import AgentState  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.chat import Evidence  # noqa: E402
from app.services import lexicon  # noqa: E402
from app.services.auth import MOCK_ANONYMOUS_ID, AuthUser, auth_tokens  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from app.services.persistence import MockPersistence  # noqa: E402
from app.services.stores import MockSemanticCache, calibrate_graph_score  # noqa: E402


def make_settings(**overrides) -> Settings:
    base = dict(
        llm_mode="fake",
        vector_mode="mock",
        graph_mode="mock",
        auth_required=False,
        dev_mode=True,
    )
    base.update(overrides)
    return Settings(**base)


def token_for(user_id: int, username: str, role: str) -> str:
    token, _ = auth_tokens.issue(
        AuthUser(id=user_id, username=username, nickname=username, role=role)
    )
    return token


class TicketOnlyLLM(FakeLLM):
    """只调 create_ticket 的假模型，用于验证 tool-only 分支的 grounding 状态。"""

    async def chat_with_tools(self, messages, tools):  # noqa: ARG002
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_ticket",
                    "name": "create_ticket",
                    "arguments": json.dumps(
                        {"title": "宿舍噪音投诉", "description": "402 夜间噪音", "category": "宿舍"},
                        ensure_ascii=False,
                    ),
                }
            ],
        }


class SettingsSafetyTests(unittest.TestCase):
    def test_defaults_require_login(self) -> None:
        settings = Settings()
        self.assertTrue(settings.auth_required, "默认必须要求登录")
        self.assertFalse(settings.dev_mode, "默认不允许匿名")

    def test_conflicting_flags_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Settings(auth_required=True, dev_mode=True)
        with self.assertRaises(ValueError):
            Settings(auth_required=False, dev_mode=False)


class AuthIsolationTests(unittest.TestCase):
    def _client(self, settings: Settings) -> tuple[TestClient, object]:
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services)), services

    def test_protected_endpoints_require_login(self) -> None:
        settings = Settings(llm_mode="fake", vector_mode="mock", graph_mode="mock")
        client, _ = self._client(settings)
        chat = client.post("/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": False})
        self.assertEqual(chat.status_code, 401)
        self.assertEqual(client.get("/api/v1/conversations").status_code, 401)

    def test_student_cannot_upload_document(self) -> None:
        settings = Settings(llm_mode="fake", vector_mode="mock", graph_mode="mock")
        client, _ = self._client(settings)
        headers = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}
        response = client.post(
            "/api/v1/documents/upload",
            json={"filename": "x.md", "content": "# 标题\n第1条 测试"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_anonymous_uses_own_identity(self) -> None:
        """dev 模式匿名不得落到 user_id=1（管理员）。"""
        settings = make_settings()
        client, services = self._client(settings)
        response = client.post(
            "/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        conversations = list(services.persistence._conversations.values())
        self.assertTrue(conversations)
        self.assertTrue(
            all(row["user_id"] == MOCK_ANONYMOUS_ID for row in conversations), conversations
        )
        self.assertFalse(any(row["user_id"] == 1 for row in conversations))


class QueryExpansionTests(unittest.TestCase):
    def test_expansion_produces_new_terms(self) -> None:
        samples = [
            "宿舍晚上几点熄灯？",
            "考试时带手机进考场会怎么处理？",
            "图书馆开放时间是什么时候？",
            "请假三天以上怎么审批？",
        ]
        changed = 0
        for query in samples:
            _, entities = lexicon.extract_intent(query)
            _, terms = lexicon.expand_query(query, entities)
            if terms:
                changed += 1
        self.assertGreaterEqual(changed, 2, "多数正式提问应能扩出同义词，而不是空转")

    def test_followup_fragment_not_injected(self) -> None:
        """追问碎片（如"那第三个呢"）不得被拼进检索串。"""
        _, terms = lexicon.expand_query("那第三个呢", ["那第三个呢"])
        self.assertEqual(terms, [])

    def test_expansion_exhausted_skips_retrieval(self) -> None:
        """扩不出词时应直接拒答，不能白跑第二轮召回。"""
        settings = make_settings()
        services = build_services(settings)
        services.llm = FakeLLM()
        spec, entry = build_graph(settings)
        engine = WorkflowEngine(spec, entry, services, settings)
        state = asyncio.run(engine.run(AgentState(query="今天天气怎么样？", user_id=1)))
        self.assertTrue(state.expansion_exhausted)
        self.assertEqual(state.retrieval_round, 1)
        self.assertEqual(engine.visited.count("parallel_retrieval"), 1)
        self.assertTrue(state.refused)


class GroundingTests(unittest.TestCase):
    def test_fabricated_article_number_is_unverified(self) -> None:
        state = AgentState(
            query="宿舍几点熄灯",
            answer="根据第99条，学生公寓22:00熄灯。",
            evidence=[
                Evidence(
                    type="vector",
                    title="《学生宿舍管理规定》",
                    detail="第四章 第18条",
                    snippet="学生公寓每日 23:30 熄灯。",
                )
            ],
        )
        result = asyncio.run(nodes.verify_grounding(state, None))
        self.assertEqual(result["grounding_status"], "unverified")
        self.assertIn("第99条", result["grounding_reason"])

    def test_citation_in_evidence_is_verified(self) -> None:
        state = AgentState(
            query="宿舍几点熄灯",
            answer="根据《学生宿舍管理规定》第18条，学生公寓每日 23:30 熄灯。",
            evidence=[
                Evidence(
                    type="vector",
                    title="《学生宿舍管理规定》",
                    detail="第四章 第18条",
                    snippet="学生公寓每日 23:30 熄灯。",
                )
            ],
        )
        result = asyncio.run(nodes.verify_grounding(state, None))
        self.assertEqual(result["grounding_status"], "verified")

    def test_tool_only_is_skipped_and_not_cached(self) -> None:
        settings = make_settings()
        services = build_services(settings)
        services.llm = TicketOnlyLLM()

        spec, entry = build_graph(settings)
        first = asyncio.run(
            WorkflowEngine(spec, entry, services, settings).run(
                AgentState(query="我要投诉宿舍噪音，帮我转人工", user_id=1)
            )
        )
        self.assertTrue(first.tool_only)
        self.assertEqual(first.grounding_status, "skipped")

        # 第二次必须重新走 tool_agent（skipped 的答案不进缓存）
        second_engine = WorkflowEngine(spec, entry, services, settings)
        second = asyncio.run(
            second_engine.run(AgentState(query="我要投诉宿舍噪音，帮我转人工", user_id=1))
        )
        self.assertFalse(second.cached)
        self.assertIn("tool_agent", second_engine.visited)


class FusionTests(unittest.TestCase):
    def test_graph_score_below_threshold_for_single_keyword(self) -> None:
        threshold = Settings().evidence_threshold
        self.assertLess(calibrate_graph_score(1, 4), threshold)
        self.assertGreaterEqual(calibrate_graph_score(2, 4), threshold)

    def test_rrf_prefers_multi_source_evidence(self) -> None:
        settings = make_settings()
        shared = Evidence(type="vector", title="《A》", detail="第1条", snippet="a", score=0.90)
        vector_only = Evidence(type="vector", title="《B》", detail="第2条", snippet="b", score=0.85)
        graph_other = Evidence(type="graph", title="知识图谱 · 《C》", detail="第3条", snippet="c", score=0.80)
        shared_graph = Evidence(type="graph", title="知识图谱 · 《A》", detail="第1条", snippet="a", score=0.60)

        state = AgentState(
            query="测试",
            vector_hits=[shared, vector_only],
            graph_paths=[graph_other, shared_graph],
        )
        ctx = NodeContext(services=None, settings=settings, bus=NullBus())
        result = asyncio.run(nodes.fuse_evidence(state, ctx))
        order = [item.title for item in result["evidence"]]
        # 两路都命中的《A》排第一；只有一路命中的《C》应排在《B》之前（RRF 看排名）
        self.assertEqual(order[0], "《A》")
        self.assertLess(order.index("知识图谱 · 《C》"), order.index("《B》"))


class RateLimitTests(unittest.TestCase):
    def _client(self, **overrides) -> TestClient:
        settings = make_settings(**overrides)
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services))

    def test_per_minute_limit(self) -> None:
        client = self._client(rate_limit_per_minute=2, rate_limit_per_day=0)
        codes = [client.get("/api/v1/conversations").status_code for _ in range(3)]
        self.assertEqual(codes[:2], [200, 200])
        self.assertEqual(codes[2], 429)

    def test_daily_quota(self) -> None:
        client = self._client(rate_limit_per_minute=0, rate_limit_per_day=1)
        first = client.post("/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": False})
        second = client.post("/api/v1/chat", json={"query": "考试带手机怎么处理？", "stream": False})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 429, second.text)

    def test_health_not_limited(self) -> None:
        client = self._client(rate_limit_per_minute=1, rate_limit_per_day=0)
        for _ in range(3):
            self.assertEqual(client.get("/health").status_code, 200)


class FusionGateTests(unittest.TestCase):
    """P1-3 质量闸门：弱图谱不得稀释向量召回。"""

    def _fuse(self, settings, vector, graph):
        state = AgentState(query="测试", vector_hits=vector, graph_paths=graph)
        ctx = NodeContext(services=None, settings=settings, bus=NullBus())
        return asyncio.run(nodes.fuse_evidence(state, ctx))

    @staticmethod
    def _vector(title: str, score: float) -> Evidence:
        return Evidence(type="vector", title=title, detail="第1条", snippet="v", score=score)

    @staticmethod
    def _graph(title: str, score: float) -> Evidence:
        return Evidence(type="graph", title=f"知识图谱 · {title}", detail="第2条", snippet="g", score=score)

    def test_weak_graph_is_gated(self) -> None:
        settings = make_settings(fusion_mode="gated", graph_quality_min_score=0.55)
        vector = [self._vector("《A》", 0.90)]
        graph = [self._graph("《B》", 0.46)]  # 只命中 1/4 关键词的弱证据
        result = self._fuse(settings, vector, graph)
        self.assertTrue(result["evidence_meta"]["graph_gated"])
        self.assertEqual(result["evidence_meta"]["graph_kept"], 0)
        self.assertEqual([item.title for item in result["evidence"]], ["《A》"])
        self.assertEqual(result["evidence_score"], 0.90)

    def test_strong_graph_participates(self) -> None:
        settings = make_settings(fusion_mode="gated", graph_quality_min_score=0.55)
        vector = [self._vector("《A》", 0.90)]
        graph = [self._graph("《B》", 0.80)]
        result = self._fuse(settings, vector, graph)
        self.assertFalse(result["evidence_meta"]["graph_gated"])
        self.assertTrue(any("《B》" in item.title for item in result["evidence"]))

    def test_rrf_mode_keeps_weak_graph(self) -> None:
        """rrf 是消融对照组：保持旧行为，弱图谱也参与融合。"""
        settings = make_settings(fusion_mode="rrf")
        result = self._fuse(settings, [self._vector("《A》", 0.90)], [self._graph("《B》", 0.46)])
        self.assertFalse(result["evidence_meta"]["graph_gated"])
        self.assertTrue(any("《B》" in item.title for item in result["evidence"]))

    def test_graph_share_is_capped(self) -> None:
        settings = make_settings(fusion_mode="gated", graph_quality_min_score=0.55, graph_max_share=0.5)
        vector = [self._vector("《A》", 0.90)]
        graph = [self._graph("《B》", 0.90), self._graph("《C》", 0.85), self._graph("《D》", 0.80)]
        result = self._fuse(settings, vector, graph)
        graph_count = sum(1 for item in result["evidence"] if item.type == "graph")
        self.assertLessEqual(graph_count, 2, "图谱条目不得超过 50% 占比")
        self.assertGreaterEqual(len(result["evidence"]), 2)


class CacheAndFeedbackTests(unittest.TestCase):
    def test_cache_ttl_expiry(self) -> None:
        cache = MockSemanticCache(ttl_seconds=60, max_entries=10)
        cache.store("测试问题", "答案", [])
        self.assertIsNotNone(cache.lookup("测试问题"))
        with patch("app.services.stores.time.time", return_value=time.time() + 61):
            self.assertIsNone(cache.lookup("测试问题"))

    def test_feedback_rejects_other_users_message(self) -> None:
        persistence = MockPersistence()
        receipt = asyncio.run(
            persistence.save_qa(AgentState(query="q", user_id=7, answer="a"))
        )
        message_id = receipt["assistant_message_id"]
        self.assertIsNone(asyncio.run(persistence.add_feedback(message_id, 8, 1, None)))
        self.assertIsNotNone(asyncio.run(persistence.add_feedback(message_id, 7, 1, None)))


if __name__ == "__main__":
    unittest.main()
