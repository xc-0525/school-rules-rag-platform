"""N-24 运营看板测试：分位/比率/趋势补齐等口径，以及权限与依赖边界。

聚合 SQL 依赖 MySQL，纯函数口径在这里覆盖；接口层验证 401/403/503。
运行：cd backend && python -m unittest tests.test_analytics -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services import analytics as an  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from tests.test_review_fixes import make_settings, token_for  # noqa: E402


class MetricTests(unittest.TestCase):
    def test_rate(self) -> None:
        self.assertEqual(an.rate(0, 0), 0.0)
        self.assertEqual(an.rate(1, 4), 0.25)
        self.assertEqual(an.rate(1, 3), 0.3333)

    def test_percentile(self) -> None:
        values = [50, 10, 40, 20, 30]
        self.assertEqual(an.percentile(values, 0.50), 30)
        self.assertEqual(an.percentile(values, 0.95), 50)
        self.assertEqual(an.percentile([], 0.95), 0)
        self.assertEqual(an.percentile([7], 0.5), 7)

    def test_fill_trend_pads_missing_days(self) -> None:
        today = datetime(2026, 11, 8, 10, 0, 0)
        rows = [
            {"day": "2026-11-08", "questions": 5, "refused": 1},
            {"day": "2026-11-06", "questions": 2, "refused": 0},
        ]
        trend = an.fill_trend(rows, 3, today=today)
        self.assertEqual([p["date"] for p in trend], ["2026-11-06", "2026-11-07", "2026-11-08"])
        self.assertEqual([p["questions"] for p in trend], [2, 0, 5])
        self.assertEqual(trend[-1]["refused"], 1)

    def test_fill_trend_ignores_unknown_days(self) -> None:
        today = datetime(2026, 11, 8)
        trend = an.fill_trend([{"day": "2026-01-01", "questions": 99}], 2, today=today)
        self.assertEqual(len(trend), 2)
        self.assertEqual(sum(p["questions"] for p in trend), 0)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        an.clear_cache()
        settings = make_settings()
        settings.auth_required = True
        settings.dev_mode = False
        services = build_services(settings)
        services.llm = FakeLLM()
        self.client = TestClient(create_app(settings, services=services))
        self.admin = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}
        self.student = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}

    def test_auth_and_role(self) -> None:
        self.assertEqual(self.client.get("/api/v1/admin/stats/overview").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/admin/stats/overview", headers=self.student).status_code, 403
        )

    def test_mock_mode_reports_mysql_missing(self) -> None:
        resp = self.client.get("/api/v1/admin/stats/overview", headers=self.admin)
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertIn("MySQL", resp.json()["detail"])

    def test_days_param_validated(self) -> None:
        resp = self.client.get("/api/v1/admin/stats/overview?days=0", headers=self.admin)
        self.assertEqual(resp.status_code, 422)
