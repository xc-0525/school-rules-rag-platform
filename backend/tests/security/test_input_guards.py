"""输入与资源防护用例（对应清单 SEC-WEB-09 上传 / SEC-WEB-19 限流）。

覆盖：上传体积上限、空内容、base64 校验、限流三层（分钟 / 日配额 / 并发闸门）。

运行：cd backend && python -m unittest tests.security.test_input_guards -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.ratelimit import RateLimiter  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.auth import AuthUser, auth_tokens  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402


def prod_settings() -> Settings:
    return Settings(
        llm_mode="fake",
        vector_mode="mock",
        graph_mode="mock",
        auth_required=True,
        dev_mode=False,
    )


def build_admin_client() -> TestClient:
    settings = prod_settings()
    services = build_services(settings)
    services.llm = FakeLLM()
    client = TestClient(create_app(settings, services=services))
    token, _ = auth_tokens.issue(AuthUser(id=1, username="admin", nickname="管理员", role="admin"))
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


class UploadGuardTests(unittest.TestCase):
    """SEC-WEB-09：上传边界（体积 / 空文件 / 编码）。"""

    def setUp(self) -> None:
        self.client = build_admin_client()

    def test_rejects_empty_text_upload(self) -> None:
        """空内容在 Pydantic 模型层就被 422 拒绝（比业务层 400 更早）。"""
        response = self.client.post(
            "/api/v1/documents/upload", json={"filename": "empty.md"}
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("content", response.text)

    def test_rejects_oversized_text_upload(self) -> None:
        """5MB 文本上限：超限 413，不得进入解析流程。"""
        big = "a" * (5 * 1024 * 1024 + 1)
        response = self.client.post(
            "/api/v1/documents/upload",
            json={"filename": "big.md", "content": big},
        )
        self.assertEqual(response.status_code, 413, response.text)

    def test_rejects_invalid_base64_for_pdf(self) -> None:
        response = self.client.post(
            "/api/v1/documents/upload",
            json={"filename": "doc.pdf", "content_base64": "!!!not-base64!!!"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("base64", response.json()["detail"])

    def test_rejects_pdf_without_payload(self) -> None:
        """PDF 缺 content_base64 同样在模型层 422 拒绝。"""
        response = self.client.post(
            "/api/v1/documents/upload", json={"filename": "doc.pdf"}
        )
        self.assertEqual(response.status_code, 422, response.text)


class RateLimiterTests(unittest.TestCase):
    """SEC-WEB-19：限流器单元行为（三层）。"""

    def test_minute_quota_blocks_excess(self) -> None:
        limiter = RateLimiter(per_minute=2, per_day=0, concurrent=0)
        self.assertTrue(limiter.hit("k")[0])
        self.assertTrue(limiter.hit("k")[0])
        allowed, retry_after = limiter.hit("k")
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 1)
        # 其他主体不受影响
        self.assertTrue(limiter.hit("other")[0])

    def test_minute_window_slides(self) -> None:
        """窗口滑过后配额恢复（用极短窗口模拟时间推进）。"""
        limiter = RateLimiter(per_minute=1, per_day=0, concurrent=0, window_seconds=1)
        self.assertTrue(limiter.hit("k")[0])
        self.assertFalse(limiter.hit("k")[0])
        import time

        time.sleep(1.05)
        self.assertTrue(limiter.hit("k")[0])

    def test_daily_quota_blocks_after_limit(self) -> None:
        limiter = RateLimiter(per_minute=0, per_day=1, concurrent=0)
        self.assertTrue(limiter.hit_daily("u1")[0])
        self.assertFalse(limiter.hit_daily("u1")[0])
        self.assertTrue(limiter.hit_daily("u2")[0], "日配额按主体隔离")

    def test_concurrency_gate_release(self) -> None:
        """同一主体在途 1 个：第二个必须被闸住；释放后可再次进入。"""
        limiter = RateLimiter(per_minute=0, per_day=0, concurrent=1)
        self.assertTrue(limiter.acquire("u1"))
        self.assertFalse(limiter.acquire("u1"))
        limiter.release("u1")
        self.assertTrue(limiter.acquire("u1"))
        limiter.release("u1")
        self.assertEqual(limiter.inflight("u1"), 0)

    def test_disabled_limits_always_allow(self) -> None:
        """per_minute=0 语义是"不限"而不是"全拒"（边界约定）。"""
        limiter = RateLimiter(per_minute=0, per_day=0, concurrent=0)
        for _ in range(5):
            self.assertTrue(limiter.hit("k")[0])
            self.assertTrue(limiter.hit_daily("k")[0])


if __name__ == "__main__":
    unittest.main()
