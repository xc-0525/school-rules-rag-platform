"""Day1 验收测试：图结构 + 三种控制结构 + API SSE。

运行：cd backend && python -m unittest tests.test_workflow -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.agents.graph import build_graph  # noqa: E402
from app.agents.runtime import WorkflowEngine  # noqa: E402
from app.agents.state import AgentState  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.chat import Evidence  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import DEFAULT_FAKE_ANSWER, FakeLLM  # noqa: E402
from app.services.stores import MockSemanticCache  # noqa: E402
from app.agents import nodes  # noqa: E402


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data += line[len("data:") :].strip()
        events.append((event, json.loads(data) if data else {}))
    return events


def make_settings() -> Settings:
    return Settings(llm_mode="fake", vector_mode="mock", graph_mode="mock")


class WorkflowEngineTests(unittest.TestCase):
    """直接驱动图引擎，验证节点、路由与循环。"""

    def _run(self, query: str, answer: str | None = None):
        settings = make_settings()
        services = build_services(settings)
        services.llm = FakeLLM(answer or DEFAULT_FAKE_ANSWER)
        nodes_spec, entry = build_graph(settings)
        engine = WorkflowEngine(nodes_spec, entry, services, settings)
        state = asyncio.run(engine.run(AgentState(query=query)))
        return state, engine

    def test_graph_shape(self) -> None:
        nodes_spec, entry = build_graph(make_settings())
        self.assertEqual(entry, "cache_lookup")
        agent_nodes = [spec for spec in nodes_spec.values() if spec.kind == "agent"]
        self.assertGreaterEqual(len(agent_nodes), 5, "课程要求：节点不少于 5 个")
        controls = {spec.control for spec in nodes_spec.values() if spec.control}
        self.assertTrue(
            {"parallel", "selection", "loop"}.issubset(controls),
            f"课程要求至少两种控制结构，当前为 {controls}",
        )
        parallel = nodes_spec["parallel_retrieval"]
        self.assertEqual(parallel.kind, "parallel")
        self.assertEqual(len(parallel.children), 2)

    def test_normal_path(self) -> None:
        state, engine = self._run("宿舍晚上几点熄灯？")
        self.assertFalse(state.refused)
        self.assertIn("熄灯", state.answer)
        self.assertGreater(len(state.sources), 0)
        self.assertEqual(state.retrieval_round, 0)
        visited = set(engine.visited)
        self.assertTrue({"parallel_retrieval", "retrieve_vector", "retrieve_graph",
                         "fuse_evidence", "generate_answer", "verify_grounding",
                         "persist_answer"}.issubset(visited))
        routes = [event for event in engine.events if event.get("phase") == "route"]
        self.assertTrue(any(route["from"] == "check_sufficiency" and route["to"] == "generate_answer"
                            for route in routes))

    def test_cache_hit_short_circuit(self) -> None:
        state, engine = self._run("宿舍几点熄灯？")
        self.assertTrue(state.cached)
        self.assertIn("persist_answer", engine.visited)
        self.assertNotIn("generate_answer", engine.visited, "缓存命中应短路，不调用大模型")

    def test_reject_path(self) -> None:
        state, engine = self._run("今天天气怎么样？")
        self.assertTrue(state.refused)
        self.assertEqual(state.answer, nodes.NO_EVIDENCE_ANSWER)
        self.assertEqual(state.retrieval_round, 2)
        self.assertIn("reject_handoff", engine.visited)
        self.assertNotIn("generate_answer", engine.visited)

    def test_expand_loop(self) -> None:
        """证据不足 → 查询扩展 → 重新召回（循环结构）。"""
        state, engine = self._run("晚上不回寝室会怎样？")
        self.assertEqual(state.retrieval_round, 1)
        self.assertIn("expand_query", engine.visited)
        self.assertGreater(len(state.evidence), 0)
        self.assertFalse(state.refused)
        self.assertIn("晚归", state.rewritten_query)

    def test_grounding_regen_loop(self) -> None:
        """引用校验不通过 → 重新生成 → 仍不通过 → 降级（第二个循环）。"""
        state, engine = self._run("考试时带手机进考场会怎么处理？", answer=DEFAULT_FAKE_ANSWER)
        self.assertTrue(state.degraded)
        self.assertEqual(state.regen_count, 2)
        self.assertEqual(engine.visited.count("generate_answer"), 2)
        self.assertIn("degrade_answer", engine.visited)
        routes = [(event["from"], event["to"]) for event in engine.events if event.get("phase") == "route"]
        self.assertIn(("verify_grounding", "generate_answer"), routes)

    def test_step_guard(self) -> None:
        """步数上限保护：把上限调到 1，图应该在第二步报错。"""
        settings = make_settings()
        settings.max_steps = 1
        services = build_services(settings)
        nodes_spec, entry = build_graph(settings)
        engine = WorkflowEngine(nodes_spec, entry, services, settings)
        with self.assertRaises(Exception):
            asyncio.run(engine.run(AgentState(query="宿舍晚上几点熄灯？")))

    def test_degraded_answer_not_cached(self) -> None:
        """降级答案不能写进缓存（修复 #2 的回归测试）。"""
        settings = make_settings()
        services = build_services(settings)
        services.llm = FakeLLM(DEFAULT_FAKE_ANSWER)  # 固定引用宿舍条例，考试问题会校验失败
        nodes_spec, entry = build_graph(settings)
        query = "考试时带手机进考场会怎么处理？"

        engine_first = WorkflowEngine(nodes_spec, entry, services, settings)
        first = asyncio.run(engine_first.run(AgentState(query=query)))
        self.assertTrue(first.degraded, "第一次应因引用校验失败而降级")

        engine_second = WorkflowEngine(nodes_spec, entry, services, settings)
        second = asyncio.run(engine_second.run(AgentState(query=query)))
        self.assertFalse(second.cached, "降级答案不应被缓存")
        self.assertIn("generate_answer", engine_second.visited, "第二次应重新走生成，而不是命中缓存")

    def test_grounding_accepts_plain_document_title(self) -> None:
        """图谱证据标题带“知识图谱 · ”前缀时，引用校验不应误判（修复 #8）。"""
        state = AgentState(
            query="宿舍几点熄灯？",
            answer="根据《测试校规》第6条，学生公寓每日23:30熄灯。",
            evidence=[
                Evidence(
                    type="graph",
                    title="知识图谱 · 测试校规",
                    detail="第一章 · 第6条（主题：宿舍管理）",
                    snippet="第6条 学生公寓每日23:30熄灯。",
                )
            ],
        )
        result = asyncio.run(nodes.verify_grounding(state, None))
        self.assertTrue(result["grounding_ok"])

    def test_cache_clear(self) -> None:
        """缓存支持清空（上传/删除文档后调用，修复 #3）。"""
        cache = MockSemanticCache()
        cache.store("测试问题？", "测试答案", [])
        self.assertIsNotNone(cache.lookup("测试问题？"))
        cache.clear()
        self.assertIsNone(cache.lookup("测试问题？"))


class ApiTests(unittest.TestCase):
    """HTTP 层：非流式 / SSE 流式 / 图定义接口。"""

    def setUp(self) -> None:
        self.settings = make_settings()
        services = build_services(self.settings)
        services.llm = FakeLLM()
        self.app = create_app(self.settings, services=services)
        self.client = TestClient(self.app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["graph"]["agent_node_count"], 5)
        self.assertTrue({"parallel", "selection", "loop"}.issubset(set(payload["graph"]["control_structures"])))

    def test_graph_endpoint(self) -> None:
        response = self.client.get("/api/v1/graph")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["entry"], "cache_lookup")
        self.assertGreaterEqual(payload["node_count"], 10)
        self.assertIn("parallel", payload["control_structures"])
        self.assertTrue(any(node["kind"] == "parallel" for node in payload["nodes"]))

    def test_document_upload_requires_real_mode(self) -> None:
        """mock 向量模式下上传接口应明确拒绝（需要真实 Qdrant 才能入库）。"""
        response = self.client.post(
            "/api/v1/documents/upload",
            json={"filename": "demo.md", "content": "# demo\n\n第1条 测试"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("mock", response.json()["detail"])

    def test_document_upload_rejects_bad_suffix(self) -> None:
        response = self.client.post(
            "/api/v1/documents/upload",
            json={"filename": "demo.docx", "content": "fake"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("暂只支持", response.json()["detail"])

    def test_pdf_upload_requires_base64(self) -> None:
        response = self.client.post(
            "/api/v1/documents/upload",
            json={"filename": "demo.pdf", "content": "not base64"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("content_base64", response.json()["detail"])

    def test_openapi_contains_multipart_route_when_installed(self) -> None:
        """安装 python-multipart 后，/documents/upload-file 应出现在 OpenAPI 中且不报错。"""
        try:
            import multipart  # noqa: F401
        except ImportError:
            self.skipTest("未安装 python-multipart")
        schema = self.client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/api/v1/documents/upload-file", schema.json()["paths"])

    def test_chat_non_stream(self) -> None:
        response = self.client.post(
            "/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("熄灯", payload["answer"])
        self.assertGreater(len(payload["sources"]), 0)
        self.assertGreater(payload["runtime"]["steps"], 0)
        self.assertIn("parallel_retrieval", payload["runtime"]["visited"])

    def test_chat_stream_sse(self) -> None:
        with self.client.stream(
            "POST", "/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": True}
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            text = "".join(response.iter_text())

        events = parse_sse(text)
        names = [event for event, _ in events]
        self.assertIn("meta", names)
        self.assertIn("delta", names)
        self.assertIn("done", names)

        traces = [payload for event, payload in events if event == "trace"]
        self.assertTrue(any(item.get("phase") == "start" and item.get("kind") == "parallel" for item in traces))
        self.assertTrue(any(item.get("phase") == "route" and item.get("from") == "check_sufficiency" for item in traces))

        done = next(payload for event, payload in events if event == "done")
        self.assertIn("熄灯", done["answer"])
        self.assertIn("parallel_retrieval", done["runtime"]["visited"])
        self.assertGreaterEqual(len(done["runtime"]["visited"]), 5)
        self.assertEqual(done["refused"], False)


class AuthTests(unittest.TestCase):
    """登录 / 鉴权 / 按用户隔离会话。"""

    def _build_client(self, auth_required: bool = True) -> TestClient:
        settings = make_settings()
        settings.auth_required = auth_required
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services))

    def _login(self, client: TestClient, username: str, password: str) -> str:
        response = client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_login_me_logout(self) -> None:
        client = self._build_client()
        token = self._login(client, "admin", "admin123")

        me = client.get("/api/v1/auth/me", headers=self._headers(token))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "admin")
        self.assertEqual(me.json()["role"], "admin")

        wrong = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "bad-password"}
        )
        self.assertEqual(wrong.status_code, 401)

        self.assertEqual(client.post("/api/v1/auth/logout", headers=self._headers(token)).status_code, 200)
        expired = client.get("/api/v1/auth/me", headers=self._headers(token))
        self.assertEqual(expired.status_code, 401)

    def test_auth_required_blocks_business_apis(self) -> None:
        client = self._build_client()
        # 未登录：业务接口 401，健康检查保持公开
        self.assertEqual(
            client.post("/api/v1/chat", json={"query": "宿舍晚上几点熄灯？", "stream": False}).status_code,
            401,
        )
        self.assertEqual(client.get("/api/v1/conversations").status_code, 401)
        self.assertEqual(client.get("/health").status_code, 200)

        admin = self._login(client, "admin", "admin123")
        chat = client.post(
            "/api/v1/chat",
            json={"query": "宿舍晚上几点熄灯？", "stream": False},
            headers=self._headers(admin),
        )
        self.assertEqual(chat.status_code, 200, chat.text)

        # 学生不能上传文档（管理员可以，mock 模式下再被 400 拒绝）
        student = self._login(client, "student", "student123")
        payload = {"filename": "demo.md", "content": "# demo\n\n第1条 测试"}
        self.assertEqual(
            client.post("/api/v1/documents/upload", json=payload, headers=self._headers(student)).status_code,
            403,
        )
        admin_upload = client.post(
            "/api/v1/documents/upload", json=payload, headers=self._headers(admin)
        )
        self.assertEqual(admin_upload.status_code, 400)
        self.assertIn("mock", admin_upload.json()["detail"])

    def test_conversation_isolation_between_users(self) -> None:
        client = self._build_client()
        admin = self._login(client, "admin", "admin123")
        student = self._login(client, "student", "student123")

        chat = client.post(
            "/api/v1/chat",
            json={"query": "宿舍晚上几点熄灯？", "stream": False},
            headers=self._headers(admin),
        )
        self.assertEqual(chat.status_code, 200, chat.text)

        admin_list = client.get("/api/v1/conversations", headers=self._headers(admin)).json()
        student_list = client.get("/api/v1/conversations", headers=self._headers(student)).json()
        self.assertTrue(admin_list)
        self.assertEqual(student_list, [])

        conversation_id = admin_list[0]["id"]
        denied = client.get(
            f"/api/v1/conversations/{conversation_id}", headers=self._headers(student)
        )
        self.assertEqual(denied.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
