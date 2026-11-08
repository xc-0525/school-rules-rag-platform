"""N-25 反馈与未命中工作台测试：归一化聚合、流转状态机、权限边界。

聚合与状态机是纯函数，不依赖 MySQL；接口层只验证权限与依赖未就绪时的错误码。
运行：cd backend && python -m unittest tests.test_feedback_workbench -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services import feedback_admin as fb  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from tests.test_review_fixes import make_settings, token_for  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_punctuation_and_case_collapsed(self) -> None:
        a = fb.normalize_query("宿舍 灯坏了！！")
        b = fb.normalize_query("宿舍灯坏了")
        self.assertEqual(a, b)
        self.assertEqual(fb.query_hash(a), fb.query_hash(b))

    def test_different_questions_differ(self) -> None:
        self.assertNotEqual(fb.query_hash("宿舍灯坏了"), fb.query_hash("宿舍门坏了"))


class GroupTests(unittest.TestCase):
    def test_group_counts_and_time_range(self) -> None:
        now = datetime(2026, 11, 1, 9, 0, 0)
        rows = [
            {"question": "宿舍灯坏了", "created_at": now},
            {"question": "宿舍 灯坏了。", "created_at": now + timedelta(days=2)},
            {"question": "宿舍灯坏了！", "created_at": now + timedelta(days=1)},
            {"question": "奖学金怎么评", "created_at": now + timedelta(hours=1)},
            {"question": "   ", "created_at": now},
        ]
        items = fb.group_unmatched(rows)
        self.assertEqual(len(items), 2)
        top = items[0]
        self.assertEqual(top["count"], 3)
        self.assertEqual(top["first_seen"], "2026-11-01 09:00:00")
        self.assertEqual(top["last_seen"], "2026-11-03 09:00:00")
        self.assertEqual(items[1]["count"], 1)


class TransitionTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        self.assertEqual(fb.check_feedback_transition("pending", "attribute", "语料缺失"), "attributed")
        self.assertEqual(
            fb.check_feedback_transition("attributed", "resolve", remark="补了宿舍管理办法第 12 条"),
            "resolved",
        )
        self.assertEqual(fb.check_feedback_transition("resolved", "verify"), "verified")

    def test_order_enforced(self) -> None:
        with self.assertRaises(fb.FeedbackRuleError):
            fb.check_feedback_transition("pending", "verify")
        with self.assertRaises(fb.FeedbackRuleError):
            fb.check_feedback_transition("verified", "verify")

    def test_attribution_required_and_enum(self) -> None:
        with self.assertRaises(fb.FeedbackRuleError):
            fb.check_feedback_transition("pending", "attribute")
        with self.assertRaises(fb.FeedbackRuleError):
            fb.check_feedback_transition("pending", "attribute", "随便写的")

    def test_resolve_requires_remark(self) -> None:
        with self.assertRaises(fb.FeedbackRuleError):
            fb.check_feedback_transition("attributed", "resolve", remark="")

    def test_unknown_action(self) -> None:
        with self.assertRaises(fb.FeedbackRuleError):
            fb.check_feedback_transition("pending", "delete")

    def test_unmatched_status_enum(self) -> None:
        self.assertEqual(fb.check_unmatched_status("ignored"), "ignored")
        with self.assertRaises(fb.FeedbackRuleError):
            fb.check_unmatched_status("done")


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = make_settings()
        settings.auth_required = True
        settings.dev_mode = False
        services = build_services(settings)
        services.llm = FakeLLM()
        self.client = TestClient(create_app(settings, services=services))
        self.admin = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}
        self.student = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}

    def test_admin_only(self) -> None:
        self.assertEqual(self.client.get("/api/v1/admin/feedback").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/feedback", headers=self.student).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/unmatched", headers=self.student).status_code, 403)

    def test_mock_mode_reports_mysql_missing(self) -> None:
        resp = self.client.get("/api/v1/admin/feedback", headers=self.admin)
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertIn("MySQL", resp.json()["detail"])

    def test_invalid_action_rejected_by_schema(self) -> None:
        resp = self.client.post(
            "/api/v1/admin/feedback/1/transition",
            json={"action": "delete"},
            headers=self.admin,
        )
        self.assertEqual(resp.status_code, 422)
