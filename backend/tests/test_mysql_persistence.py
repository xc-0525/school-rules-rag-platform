"""D2 集成测试：MySQL 落库 + 会话历史 + 点赞/点踩。

MySQL 不可用时自动跳过，不影响其他单元测试。
运行：cd backend && python -m unittest tests.test_mysql_persistence -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402

MYSQL_URL = os.getenv(
    "MYSQL_URL", "mysql+pymysql://root:@127.0.0.1:3306/school_rules_rag?charset=utf8mb4"
)


def mysql_available() -> bool:
    try:
        import pymysql

        connection = pymysql.connect(
            host="127.0.0.1", port=3306, user="root", password="", charset="utf8mb4",
            database="school_rules_rag", connect_timeout=2,
        )
        connection.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def cleanup_conversation(conversation_id: int) -> None:
    """删除测试会话（外键级联会清掉消息和反馈）。"""
    try:
        import pymysql

        connection = pymysql.connect(
            host="127.0.0.1", port=3306, user="root", password="", charset="utf8mb4",
            database="school_rules_rag", autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
        connection.close()
    except Exception:  # noqa: BLE001
        pass


@unittest.skipUnless(mysql_available(), "本机 MySQL / school_rules_rag 不可用，跳过集成测试")
class MySQLPersistenceTests(unittest.TestCase):
    """真实 MySQL 的端到端测试：问答 → 落库 → 历史 → 反馈。"""

    def setUp(self) -> None:
        self.settings = Settings(llm_mode="fake", mysql_url=MYSQL_URL)
        services = build_services(self.settings)
        services.llm = FakeLLM()
        self.app = create_app(self.settings, services=services)
        self.client = TestClient(self.app)
        self.created_conversations: list[int] = []

    def tearDown(self) -> None:
        for conversation_id in self.created_conversations:
            cleanup_conversation(conversation_id)

    def _chat(self, query: str, conversation_id: int | None = None) -> dict:
        payload = {"query": query, "stream": False}
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        response = self.client.post("/api/v1/chat", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        if data.get("conversation_id"):
            self.created_conversations.append(data["conversation_id"])
        return data

    def test_full_flow(self) -> None:
        # 1) 首次提问：自动建会话 + 落两条消息
        data = self._chat("宿舍晚上几点熄灯？")
        conversation_id = data["conversation_id"]
        self.assertIsInstance(conversation_id, int)
        self.assertGreater(len(data["sources"]), 0)

        # 2) 会话列表能看到
        response = self.client.get("/api/v1/conversations")
        self.assertEqual(response.status_code, 200, response.text)
        conversations = response.json()
        target = next((item for item in conversations if item["id"] == conversation_id), None)
        self.assertIsNotNone(target)
        self.assertEqual(target["message_count"], 2)

        # 3) 会话详情：user + assistant 两条消息，assistant 带来源
        response = self.client.get(f"/api/v1/conversations/{conversation_id}")
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(len(detail["messages"]), 2)
        self.assertEqual(detail["messages"][0]["role"], "user")
        assistant = detail["messages"][1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertGreater(len(assistant["sources"]), 0)
        self.assertIn("熄灯", assistant["content"])

        # 4) 点赞，然后改成点踩（唯一键 upsert）
        response = self.client.post(
            f"/api/v1/messages/{assistant['id']}/feedback", json={"rating": 1, "comment": "有用"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        first = response.json()
        response = self.client.post(
            f"/api/v1/messages/{assistant['id']}/feedback", json={"rating": -1}
        )
        second = response.json()
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["rating"], -1)

        # 5) 同一个会话继续追问，消息变成 4 条
        self._chat("那晚归会怎么处理？", conversation_id=conversation_id)
        detail = self.client.get(f"/api/v1/conversations/{conversation_id}").json()
        self.assertEqual(len(detail["messages"]), 4)

    def test_unknown_conversation_404(self) -> None:
        response = self.client.get("/api/v1/conversations/999999999")
        self.assertEqual(response.status_code, 404)

    def test_unknown_message_feedback_404(self) -> None:
        response = self.client.post("/api/v1/messages/999999999/feedback", json={"rating": 1})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
