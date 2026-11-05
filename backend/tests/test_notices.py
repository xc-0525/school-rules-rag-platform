"""公告模块测试：有效期过滤、分类、管理与权限。

用内存实现（make_settings 不配 mysql_url），不依赖 MySQL。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services import notices as notice_service  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402
from tests.test_review_fixes import make_settings, token_for  # noqa: E402


class NoticeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        notice_service.clear_mock_notices()
        self.settings = make_settings()

    def _create(self, **overrides) -> dict:
        payload = {
            "title": "关于本周六停水的通知",
            "content": "因管网检修，本周六 08:00-12:00 全校停水，请提前储水。",
            "category": "water",
            **overrides,
        }
        return notice_service.create_notice(self.settings, 1, payload)

    def test_create_and_list(self) -> None:
        created = self._create()
        self.assertEqual(created["category"], "water")
        self.assertEqual(created["category_label"], "停水")
        items = notice_service.list_notices(self.settings)
        self.assertTrue(any(item["title"] == "关于本周六停水的通知" for item in items))

    def test_category_filter(self) -> None:
        self._create(category="water")
        self._create(title="关于停电的通知", content="周五停电", category="power")
        water = notice_service.list_notices(self.settings, category="water")
        self.assertTrue(all(item["category"] == "water" for item in water))
        self.assertFalse(any(item["category"] == "power" for item in water))

    def test_keyword_filter(self) -> None:
        self._create()
        hits = notice_service.list_notices(self.settings, keyword="停水")
        self.assertTrue(any("停水" in item["title"] for item in hits))
        none = notice_service.list_notices(self.settings, keyword="不存在的关键词XYZ")
        self.assertEqual(none, [])

    def test_expired_notice_hidden_by_default(self) -> None:
        now = datetime.now()
        self._create(
            title="已经结束的停水通知",
            effective_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        visible = notice_service.list_notices(self.settings, keyword="已经结束")
        self.assertEqual(visible, [], "过期公告不应出现在默认列表")
        with_expired = notice_service.list_notices(
            self.settings, keyword="已经结束", include_expired=True
        )
        self.assertEqual(len(with_expired), 1, "include_expired=True 时应能看到")
        self.assertTrue(with_expired[0]["is_expired"])

    def test_invalid_expiry_rejected(self) -> None:
        now = datetime.now()
        with self.assertRaises(ValueError):
            self._create(effective_at=now, expires_at=now - timedelta(hours=1))

    def test_update_and_delete(self) -> None:
        created = self._create()
        notice_id = created["id"]
        updated = notice_service.update_notice(
            self.settings, notice_id, {"is_pinned": True, "title": "停水通知（更新）"}
        )
        self.assertIsNotNone(updated)
        self.assertTrue(updated["is_pinned"])
        self.assertEqual(updated["title"], "停水通知（更新）")

        self.assertTrue(notice_service.delete_notice(self.settings, notice_id))
        self.assertIsNone(notice_service.get_notice(self.settings, notice_id))

    def test_pinned_first(self) -> None:
        self._create(title="普通公告")
        self._create(title="置顶公告", is_pinned=True)
        items = notice_service.list_notices(self.settings)
        titles = [item["title"] for item in items]
        self.assertLess(titles.index("置顶公告"), titles.index("普通公告"), "置顶应排在前")

    def test_search_only_returns_published(self) -> None:
        self._create(title="草稿公告", status="draft")
        hits = notice_service.search_notices(self.settings, keyword="草稿公告")
        self.assertEqual(hits, [], "问答检索不应看到草稿")

    # ---------------- 紧急横幅 ----------------

    def test_banner_only_returns_urgent_pinned(self) -> None:
        self._create(title="普通公告")
        self._create(title="置顶但不紧急", is_pinned=True)
        self._create(title="紧急停水通知", priority="urgent")
        banners = notice_service.list_banner_notices(self.settings)
        titles = [item["title"] for item in banners]
        self.assertEqual(titles, ["紧急停水通知"], "横幅只收 urgent + 置顶")
        self.assertTrue(banners[0]["is_pinned"], "urgent 创建时自动置顶")
        self.assertTrue(banners[0]["is_urgent"])

    def test_banner_hides_expired_and_draft(self) -> None:
        now = datetime.now()
        self._create(
            title="已结束的紧急通知",
            priority="urgent",
            effective_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        self._create(title="草稿紧急通知", priority="urgent", status="draft")
        self.assertEqual(
            notice_service.list_banner_notices(self.settings),
            [],
            "过期与草稿的紧急公告都不应上横幅",
        )

    def test_banner_hides_not_yet_effective(self) -> None:
        now = datetime.now()
        self._create(
            title="明天才开始生效的紧急通知",
            priority="urgent",
            effective_at=now + timedelta(days=1),
        )
        self.assertEqual(notice_service.list_banner_notices(self.settings), [])

    def test_urgent_update_downgrade_keeps_pinned(self) -> None:
        created = self._create(title="紧急通知", priority="urgent")
        updated = notice_service.update_notice(
            self.settings, created["id"], {"priority": "normal"}
        )
        self.assertEqual(updated["priority"], "normal")
        self.assertFalse(updated["is_urgent"])
        # 降级不强制取消置顶，由管理员自行决定
        self.assertTrue(updated["is_pinned"])

    def test_normal_priority_default(self) -> None:
        created = self._create()
        self.assertEqual(created["priority"], "normal")
        self.assertFalse(created["is_urgent"])
        self.assertEqual(notice_service.list_banner_notices(self.settings), [])


class NoticeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        notice_service.clear_mock_notices()

    def _client(self):
        # 必须关掉 dev_mode：它会放行匿名访问并跳过 admin 检查，
        # 那样 401/403 的断言就测不到了（dev_mode 只给本地联调用）
        settings = make_settings()
        settings.auth_required = True
        settings.dev_mode = False
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services)), settings

    def test_student_can_read_but_not_write(self) -> None:
        client, _ = self._client()
        student = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}

        listed = client.get("/api/v1/notices", headers=student)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertGreaterEqual(len(listed.json()), 1, "应能看到种子公告")

        created = client.post(
            "/api/v1/notices",
            json={"title": "学生尝试发布", "content": "x", "category": "other"},
            headers=student,
        )
        self.assertEqual(created.status_code, 403)

    def test_admin_can_create_and_update(self) -> None:
        client, _ = self._client()
        admin = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}

        created = client.post(
            "/api/v1/notices",
            json={
                "title": "关于本周日停电的通知",
                "content": "本周日 09:00-17:00 东区停电。",
                "category": "power",
                "is_pinned": True,
            },
            headers=admin,
        )
        self.assertEqual(created.status_code, 201, created.text)
        notice_id = created.json()["id"]

        patched = client.patch(
            f"/api/v1/notices/{notice_id}", json={"status": "draft"}, headers=admin
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["status"], "draft")

        deleted = client.delete(f"/api/v1/notices/{notice_id}", headers=admin)
        self.assertEqual(deleted.status_code, 204)

    def test_categories_endpoint(self) -> None:
        client, _ = self._client()
        student = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}
        response = client.get("/api/v1/notices/meta/categories", headers=student)
        self.assertEqual(response.status_code, 200, response.text)
        codes = {item["code"] for item in response.json()}
        self.assertIn("water", codes)
        self.assertIn("power", codes)

    def test_banner_endpoint(self) -> None:
        client, _ = self._client()
        admin = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}
        student = {"Authorization": f"Bearer {token_for(2, 'student', 'student')}"}

        created = client.post(
            "/api/v1/notices",
            json={
                "title": "紧急停水",
                "content": "今晚 22:00 起停水。",
                "category": "water",
                "priority": "urgent",
            },
            headers=admin,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue(created.json()["is_pinned"], "urgent 应自动置顶")

        banners = client.get("/api/v1/notices/banner", headers=student)
        self.assertEqual(banners.status_code, 200, banners.text)
        titles = [item["title"] for item in banners.json()]
        self.assertIn("紧急停水", titles)

        # 学生能看到横幅但不能管理
        forbidden = client.post(
            "/api/v1/notices",
            json={"title": "x", "content": "y", "priority": "urgent"},
            headers=student,
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_requires_login(self) -> None:
        client, _ = self._client()
        self.assertEqual(client.get("/api/v1/notices").status_code, 401)
        self.assertEqual(client.get("/api/v1/notices/banner").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
