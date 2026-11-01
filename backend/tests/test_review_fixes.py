"""审查整改回归测试（P0-1 / P0-4 / P0-5 / P1-3 / P1-7 / P1-8）。

运行：cd backend && python -m unittest tests.test_review_fixes -v
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from app.agents.tools import ToolRunner  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from app.services.persistence import MockPersistence  # noqa: E402
from tests.test_tools_memory import build_services_for_test  # noqa: E402
from app.services.stores import MockSemanticCache, calibrate_graph_score  # noqa: E402
from app.services.tickets import clear_mock_tickets  # noqa: E402


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
        settings = make_settings(fusion_mode="rrf")
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


class SufficiencyJudgeTests(unittest.TestCase):
    """证据充分性判定：解决"可答题与无关题分数区间重叠"导致的拒答失效。"""

    class _CountingLLM(FakeLLM):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.calls = 0

        async def judge_sufficiency(self, question, evidence):
            self.calls += 1
            return await super().judge_sufficiency(question, evidence)

    class _BrokenLLM(FakeLLM):
        async def judge_sufficiency(self, question, evidence):
            raise RuntimeError("judge boom")

    @staticmethod
    def _evidence(text: str = "第5条 宿舍楼晚上十一点锁门、熄灯") -> list[Evidence]:
        return [Evidence(type="vector", title="《住宿规定》", detail="正文 · 第5条", snippet=text, score=0.6)]

    def _run(self, settings, score: float, llm=None, evidence=None) -> dict:
        services = build_services(settings)
        services.llm = llm or FakeLLM()
        state = AgentState(
            query="校医院在哪儿？", evidence=evidence if evidence is not None else self._evidence(),
            evidence_score=score,
        )
        ctx = NodeContext(services=services, settings=settings, bus=NullBus())
        return asyncio.run(nodes.check_sufficiency(state, ctx))

    def test_high_score_short_circuits_without_model(self) -> None:
        llm = self._CountingLLM()
        result = self._run(make_settings(), 0.90, llm)
        self.assertEqual(result["sufficiency_verdict"], "sufficient")
        self.assertEqual(llm.calls, 0, "高置信度不应调用模型")

    def test_low_score_marks_insufficient_without_model(self) -> None:
        llm = self._CountingLLM()
        result = self._run(make_settings(), 0.40, llm)
        self.assertEqual(result["sufficiency_verdict"], "insufficient")
        self.assertEqual(llm.calls, 0, "低分不应调用模型")

    def test_gray_zone_calls_model(self) -> None:
        llm = self._CountingLLM(sufficiency=False, sufficiency_reason="条款未涉及校医院")
        result = self._run(make_settings(), 0.60, llm)
        self.assertEqual(llm.calls, 1, "灰区必须调用模型判定")
        self.assertEqual(result["sufficiency_verdict"], "insufficient")
        self.assertIn("校医院", result["sufficiency_reason"])

    def test_no_evidence_is_insufficient(self) -> None:
        result = self._run(make_settings(), 0.0, evidence=[])
        self.assertEqual(result["sufficiency_verdict"], "insufficient")

    def test_judge_exception_falls_back_to_skipped(self) -> None:
        result = self._run(make_settings(), 0.60, self._BrokenLLM())
        self.assertEqual(result["sufficiency_verdict"], "skipped", "判定失败不能误拒答")

    def test_judge_off_keeps_old_behaviour(self) -> None:
        llm = self._CountingLLM(sufficiency=False)
        result = self._run(make_settings(sufficiency_judge="off"), 0.60, llm)
        self.assertEqual(result["sufficiency_verdict"], "skipped")
        self.assertEqual(llm.calls, 0)

    def test_route_expands_then_rejects(self) -> None:
        settings = make_settings(sufficiency_judge="auto", max_retrieval_rounds=2)
        spec, _ = build_graph(settings)
        route = spec["check_sufficiency"].route
        self.assertIsNotNone(route, "充分性节点必须带路由决策")

        base = AgentState(
            query="校医院在哪儿？", evidence=self._evidence(), evidence_score=0.62,
            sufficiency_verdict="insufficient", sufficiency_reason="条款未涉及校医院",
        )
        first, _ = route(base)
        self.assertEqual(first, "expand_query", "首轮不足应尝试扩展")

        exhausted = base.model_copy(update={"retrieval_round": 2})
        final, reason = route(exhausted)
        self.assertEqual(final, "reject_handoff", "扩展耗尽后应拒答")
        self.assertIn("校医院", reason)

    def test_route_generates_when_sufficient(self) -> None:
        settings = make_settings()
        spec, _ = build_graph(settings)
        route = spec["check_sufficiency"].route
        state = AgentState(
            query="宿舍几点熄灯？", evidence=self._evidence(), evidence_score=0.75,
            sufficiency_verdict="sufficient", sufficiency_reason="条款直接回答",
        )
        target, _ = route(state)
        self.assertEqual(target, "generate_answer")


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

    def test_append_keeps_vector_order_and_puts_graph_last(self) -> None:
        """append 模式：向量定排序，图谱只追加未命中条款（实测 MRR 不劣于仅向量）。"""
        settings = make_settings(fusion_mode="append")
        vector = [self._vector("《A》", 0.90), self._vector("《B》", 0.80)]
        graph = [self._graph("《C》", 0.95), self._graph("《D》", 0.90)]
        result = self._fuse(settings, vector, graph)
        titles = [item.title for item in result["evidence"]]
        self.assertEqual(titles[:2], ["《A》", "《B》"], "向量顺序必须保持不变")
        self.assertTrue(titles[-1].startswith("知识图谱"), "图谱证据应排在末尾")
        self.assertEqual(result["evidence_meta"]["fusion_mode"], "append")

    def test_append_skips_graph_item_already_in_vector(self) -> None:
        """同一条款已被向量召回时，图谱不再重复追加（按正文去重）。"""
        settings = make_settings(fusion_mode="append")
        vector = [self._vector("《A》", 0.90)]
        # 真实场景下同一条款的正文一致，只是图谱多了标题前缀
        graph = [
            Evidence(
                type="graph", title="知识图谱 · 《A》", detail="第1条", snippet="v", score=0.95
            )
        ]
        result = self._fuse(settings, vector, graph)
        self.assertEqual(len(result["evidence"]), 1, "同一条款不应重复进入证据列表")
        self.assertEqual(result["evidence_meta"]["graph_kept"], 0)

    def test_append_respects_share_cap(self) -> None:
        settings = make_settings(fusion_mode="append", graph_max_share=0.5)
        vector = [self._vector("《A》", 0.90)]
        graph = [self._graph("《B》", 0.95), self._graph("《C》", 0.90), self._graph("《D》", 0.85)]
        result = self._fuse(settings, vector, graph)
        self.assertLessEqual(result["evidence_meta"]["graph_kept"], 2)

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


class TicketFlowTests(unittest.TestCase):
    """P1-7 补完：草稿不落库 → 用户确认后走独立接口 → 幂等。"""

    def setUp(self) -> None:
        clear_mock_tickets()

    def _client(self):
        settings = make_settings()
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services))

    def test_draft_surfaces_in_agent_state(self) -> None:
        """图执行完后草稿进入 state.tool_drafts，done 事件据此下发给前端。"""
        settings = make_settings()
        services = build_services(settings)
        services.llm = TicketOnlyLLM()
        spec, entry = build_graph(settings)
        state = asyncio.run(
            WorkflowEngine(spec, entry, services, settings).run(
                AgentState(query="宿舍晚上几点熄灯？", user_id=1)
            )
        )
        self.assertTrue(state.tool_only)
        self.assertEqual(len(state.tool_drafts), 1)
        self.assertEqual(state.tool_drafts[0]["status"], "draft")
        self.assertNotIn("ticket_no", state.tool_drafts[0])

    def test_draft_endpoint_creates_ticket_idempotently(self) -> None:
        client = self._client()
        payload = {
            "title": "宿舍噪音投诉",
            "description": "402 夜间噪音较大",
            "category": "宿舍",
        }
        first = client.post("/api/v1/tickets", json=payload)
        second = client.post("/api/v1/tickets", json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertRegex(first.json()["ticket_no"], r"^T\d{8}[A-Z0-9]+$")
        self.assertEqual(first.json()["reused"], False)
        self.assertEqual(second.json()["ticket_no"], first.json()["ticket_no"])
        self.assertTrue(second.json()["reused"], "窗口内重复提交应复用同一工单")

    def test_ticket_endpoint_requires_login(self) -> None:
        settings = Settings(llm_mode="fake", vector_mode="mock", graph_mode="mock")
        services = build_services(settings)
        client = TestClient(create_app(settings, services=services))
        response = client.post(
            "/api/v1/tickets",
            json={"title": "t", "description": "d", "category": "宿舍"},
        )
        self.assertEqual(response.status_code, 401)

    def test_draft_not_written_until_confirmed(self) -> None:
        """无明确意图时只出草稿，不落库；确认后才生成工单。"""
        settings, services = build_services_for_test()
        runner = ToolRunner(services, settings)
        draft = asyncio.run(
            runner.run(
                "create_ticket",
                {"title": "宿舍噪音", "description": "402 夜间较吵", "category": "宿舍"},
                user_id=901,
                conversation_id=None,
                run_id="draft-run",
                query="宿舍晚上几点熄灯？",
            )
        )
        self.assertEqual(draft.payload["status"], "draft")
        self.assertNotIn("ticket_no", draft.payload)
        self.assertEqual(draft.payload["title"], "宿舍噪音")

        # 走确认接口后才产生工单号
        settings2 = make_settings()
        client = TestClient(create_app(settings2, services=build_services(settings2)))
        confirmed = client.post(
            "/api/v1/tickets",
            json={
                "title": draft.payload["title"],
                "description": draft.payload["description"],
                "category": draft.payload["category"],
            },
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.text)
        self.assertIn("ticket_no", confirmed.json())


class UploadRawTests(unittest.TestCase):
    """P2-11：原始字节流上传——先鉴权、边收边落盘、超限即止。"""

    def _client(self, dev_mode: bool = False):
        if dev_mode:
            settings = make_settings()
        else:
            settings = Settings(llm_mode="fake", vector_mode="mock", graph_mode="mock")
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services))

    @staticmethod
    def _upload_dir_files() -> set[str]:
        from app.api.v1.documents import UPLOAD_DIR

        if not UPLOAD_DIR.is_dir():
            return set()
        return {item.name for item in UPLOAD_DIR.iterdir() if item.is_file()}

    def test_requires_login(self) -> None:
        client = self._client()
        response = client.post(
            "/api/v1/documents/upload-raw?filename=a.md",
            content="# t\n第1条 x".encode("utf-8"),
        )
        self.assertEqual(response.status_code, 401)

    def test_student_forbidden(self) -> None:
        client = self._client()
        headers = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}
        response = client.post(
            "/api/v1/documents/upload-raw?filename=a.md",
            content=b"# t",
            headers=headers,
        )
        self.assertEqual(response.status_code, 403)

    def test_oversize_is_rejected_and_cleaned(self) -> None:
        before = self._upload_dir_files()
        client = self._client(dev_mode=True)
        with patch("app.api.v1.documents.MAX_PDF_BYTES", 1024):
            response = client.post(
                "/api/v1/documents/upload-raw?filename=big.pdf",
                content=b"x" * 4096,
                headers={"Content-Type": "application/octet-stream"},
            )
        self.assertEqual(response.status_code, 413, response.text)
        # 半成品必须被删除，不能留在 uploads 目录里
        self.assertEqual(self._upload_dir_files(), before)

    def test_unsupported_suffix_rejected(self) -> None:
        client = self._client(dev_mode=True)
        response = client.post(
            "/api/v1/documents/upload-raw?filename=a.exe", content=b"x"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("暂只支持", response.json()["detail"])

    def test_valid_stream_reaches_ingest(self) -> None:
        """mock 向量模式下应停在"无法入库"的提示，说明流式接收与解析入口已走通。"""
        client = self._client(dev_mode=True)
        response = client.post(
            "/api/v1/documents/upload-raw?filename=a.md",
            content="# 测试\n第1条 测试条款".encode("utf-8"),
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("mock 向量模式", response.json()["detail"])


class LLMFailurePersistenceTests(unittest.TestCase):
    """未配置 API Key 时，提问记录仍必须按用户落库（回归：报错导致整段记录丢失）。"""

    @staticmethod
    def _no_key_settings(**overrides) -> Settings:
        base = dict(
            vector_mode="mock",
            graph_mode="mock",
            llm_mode="deepseek",
            deepseek_api_key="",
            auth_required=False,
            dev_mode=True,
        )
        base.update(overrides)
        return Settings(**base)

    def test_engine_falls_back_and_persists(self) -> None:
        settings = self._no_key_settings()
        services = build_services(settings)
        spec, entry = build_graph(settings)
        state = asyncio.run(
            WorkflowEngine(spec, entry, services, settings).run(
                AgentState(query="宿舍晚上几点熄灯？", user_id=901)
            )
        )
        self.assertTrue(state.llm_failed, "未配置 Key 时应标记 llm_failed")
        self.assertTrue(state.degraded)
        self.assertIn("不可用", state.answer)
        self.assertIsNotNone(state.conversation_id, "必须落库")
        self.assertGreater(len(state.sources), 0, "检索到的来源应保留")

        conversations = services.persistence._conversations
        self.assertEqual(len(conversations), 1)
        self.assertEqual(next(iter(conversations.values()))["user_id"], 901)

    def test_api_returns_degraded_instead_of_losing_record(self) -> None:
        settings = self._no_key_settings()
        services = build_services(settings)
        client = TestClient(create_app(settings, services=services))

        response = client.post(
            "/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["llm_failed"])
        self.assertTrue(body["degraded"])
        self.assertIsNotNone(body["conversation_id"])

        history = client.get("/api/v1/conversations").json()
        self.assertEqual(len(history), 1, "会话历史里应能看到这条记录")

    def test_two_users_keep_their_own_records(self) -> None:
        settings = self._no_key_settings(auth_required=True, dev_mode=False)
        services = build_services(settings)
        client = TestClient(create_app(settings, services=services))
        headers_a = {"Authorization": f"Bearer {token_for(11, 'alice', 'student')}"}
        headers_b = {"Authorization": f"Bearer {token_for(12, 'bob', 'student')}"}

        client.post("/api/v1/chat", json={"query": "宿舍晚上几点熄灯？"}, headers=headers_a)
        client.post("/api/v1/chat", json={"query": "图书馆几点开门？"}, headers=headers_b)

        list_a = client.get("/api/v1/conversations", headers=headers_a).json()
        list_b = client.get("/api/v1/conversations", headers=headers_b).json()
        self.assertEqual(len(list_a), 1)
        self.assertEqual(len(list_b), 1)
        self.assertNotEqual(list_a[0]["id"], list_b[0]["id"])
        # 列表接口按当前用户过滤，返回体不含 user_id，这里直接核对落库归属
        owners = {row["user_id"] for row in services.persistence._conversations.values()}
        self.assertEqual(owners, {11, 12})
        self.assertIn("熄灯", list_a[0]["title"])
        self.assertIn("图书馆", list_b[0]["title"])

    def test_unexpected_error_still_persists(self) -> None:
        """检索层抛异常时，兜底逻辑也要留下一条记录。"""
        settings = self._no_key_settings()

        class BrokenVector:
            async def search(self, query: str, top_k: int = 3):
                raise RuntimeError("qdrant down")

        services = build_services(settings)
        services.vector = BrokenVector()
        client = TestClient(create_app(settings, services=services))

        response = client.post(
            "/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": False}
        )
        self.assertEqual(response.status_code, 502, response.text)
        conversations = services.persistence._conversations
        self.assertEqual(len(conversations), 1, "异常也应留下记录")
        self.assertEqual(next(iter(conversations.values()))["user_id"], 9001)


class RequestIdTests(unittest.TestCase):
    """请求级 trace_id：响应头回显、写进日志、随 done 下发。"""

    def _client(self):
        settings = make_settings()
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services))

    def test_header_is_generated_and_echoed(self) -> None:
        client = self._client()
        generated = client.get("/health").headers.get("X-Request-ID")
        self.assertTrue(generated, "未带请求头时应自动生成")
        echoed = client.get("/health", headers={"X-Request-ID": "trace-abc"}).headers.get(
            "X-Request-ID"
        )
        self.assertEqual(echoed, "trace-abc")

    def test_chat_response_and_done_carry_request_id(self) -> None:
        client = self._client()
        headers = {"X-Request-ID": "trace-xyz"}
        body = client.post(
            "/api/v1/chat",
            json={"query": "宿舍晚上几点熄灯？", "stream": False},
            headers=headers,
        ).json()
        self.assertEqual(body["request_id"], "trace-xyz")

        stream = client.post(
            "/api/v1/chat",
            json={"query": "宿舍晚上几点熄灯？", "stream": True},
            headers=headers,
        )
        done = ""
        for block in stream.text.split("\n\n"):
            if block.startswith("event: done"):
                done = block
        self.assertIn("trace-xyz", done)

    def test_log_filter_injects_request_id(self) -> None:
        from app.core.request_context import RequestIdFilter, set_request_id

        set_request_id("trace-log")
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, "trace-log")


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
