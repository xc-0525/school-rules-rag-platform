"""访问控制用例（对应清单 SEC-WEB-06 越权 / IDOR）。

覆盖：未登录 401、伪造/畸形 token 401、学生越权写文档 403、
文件名穿越不落盘、伪造 admin 签名被拒、会话 IDOR 跨用户 404。

运行：cd backend && python -m unittest tests.security.test_access_control -v
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.auth import AuthTokenStore, AuthUser, auth_tokens  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402

_FOREIGN_SECRET = b"sec-audit-attacker-controlled-secret"


def prod_settings() -> Settings:
    """生产等价态：必须登录、无匿名兜底（auth_required=True + dev_mode=False）。"""
    return Settings(
        llm_mode="fake",
        vector_mode="mock",
        graph_mode="mock",
        auth_required=True,
        dev_mode=False,
    )


def dev_settings() -> Settings:
    return Settings(
        llm_mode="fake",
        vector_mode="mock",
        graph_mode="mock",
        auth_required=False,
        dev_mode=True,
    )


def build(settings: Settings):
    services = build_services(settings)
    services.llm = FakeLLM()
    return TestClient(create_app(settings, services=services)), services


def token_for(user_id: int, username: str, role: str, store=None) -> str:
    issue = (store or auth_tokens).issue
    token, _ = issue(AuthUser(id=user_id, username=username, nickname=username, role=role))
    return token


class AuthenticationBoundaryTests(unittest.TestCase):
    """先验"门锁"：所有受保护接口在无凭据/坏凭据时必须 401。"""

    def test_documents_require_login(self) -> None:
        client, _ = build(prod_settings())
        response = client.get("/api/v1/documents")
        self.assertEqual(response.status_code, 401, response.text)

    def test_conversations_require_login(self) -> None:
        client, _ = build(prod_settings())
        response = client.get("/api/v1/conversations")
        self.assertEqual(response.status_code, 401, response.text)

    def test_malformed_token_rejected(self) -> None:
        client, _ = build(prod_settings())
        for bad in ["garbage", "abc.def", "Bearer", "Bearer ", ""]:
            response = client.get(
                "/api/v1/documents", headers={"Authorization": bad}
            )
            self.assertEqual(
                response.status_code, 401, msg=f"坏凭据应 401: {bad!r}"
            )

    def test_anonymous_is_not_admin(self) -> None:
        """DEV_MODE 匿名身份必须是 student，绝不能继承管理员。"""
        from app.services.auth import anonymous_user

        settings = dev_settings()
        user = anonymous_user(settings)
        self.assertFalse(user.is_admin)
        self.assertNotEqual(user.id, 1, "匿名不得复用 user_id=1（admin）")


class RoleEscalationTests(unittest.TestCase):
    """垂直越权：学生不得执行管理员动作；角色信任签名而非请求参数。"""

    def setUp(self) -> None:
        self.client, _ = build(prod_settings())
        self.student_headers = {
            "Authorization": f"Bearer {token_for(2001, 'stu01', 'student')}"
        }
        self.admin_headers = {
            "Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"
        }

    def test_student_cannot_upload_document(self) -> None:
        response = self.client.post(
            "/api/v1/documents/upload",
            json={"filename": "note.md", "content": "第一条 测试内容"},
            headers=self.student_headers,
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_student_cannot_delete_document(self) -> None:
        response = self.client.delete(
            "/api/v1/documents/1", headers=self.student_headers
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_upload_suffix_whitelist_for_admin(self) -> None:
        """管理员通道也必须过后缀白名单（可执行/可渲染后缀一律拒绝）。"""
        for filename in ["shell.php", "page.html", "x.svg", "run.js", "v.exe"]:
            response = self.client.post(
                "/api/v1/documents/upload",
                json={"filename": filename, "content": "x"},
                headers=self.admin_headers,
            )
            self.assertEqual(response.status_code, 400, msg=f"{filename} 应被拒绝")
            self.assertNotEqual(response.status_code, 500)

    def test_forged_admin_token_rejected(self) -> None:
        """攻击者自签的 admin token（密钥不对）必须 401——角色只信签名。"""
        rogue_store = AuthTokenStore(secret=_FOREIGN_SECRET)
        forged = token_for(1, "admin", "admin", store=rogue_store)
        response = self.client.get(
            "/api/v1/documents", headers={"Authorization": f"Bearer {forged}"}
        )
        self.assertEqual(response.status_code, 401, response.text)


class IdorTests(unittest.TestCase):
    """水平越权：会话对象必须校验归属（A 的会话 B 访问 → 404）。"""

    def setUp(self) -> None:
        self.client, services = build(dev_settings())
        self.persistence = services.persistence
        # 直接注入一条属于用户 777 的会话（Mock 存储，自包含）
        self.persistence._conversations[42] = {
            "id": 42,
            "user_id": 777,
            "title": "A 的会话",
            "created_at": "2026-01-01 08:00:00",
            "updated_at": "2026-01-01 08:00:00",
            "message_count": 0,
        }

    def test_owner_can_read_own_conversation(self) -> None:
        owner = {"Authorization": f"Bearer {token_for(777, 'usera', 'student')}"}
        response = self.client.get("/api/v1/conversations/42", headers=owner)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], 42)

    def test_other_user_gets_404_not_data(self) -> None:
        intruder = {"Authorization": f"Bearer {token_for(888, 'userb', 'student')}"}
        response = self.client.get("/api/v1/conversations/42", headers=intruder)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertNotIn("A 的会话", response.text)

    def test_admin_role_does_not_bypass_ownership(self) -> None:
        """管理员读他人会话同样 404：对象归属校验不因角色放开（按本项目约定）。"""
        admin = {"Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"}
        response = self.client.get("/api/v1/conversations/42", headers=admin)
        self.assertIn(response.status_code, (403, 404))


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return _FakeResult(self._value)


class TokenStateTests(unittest.TestCase):
    """P0-3：用户级 token 吊销 + 60s 缓存的账号活性检查。"""

    def setUp(self) -> None:
        from app.services import auth as auth_module

        self.auth_module = auth_module
        auth_module._active_cache.clear()

    def test_revoke_all_for_user_kills_old_tokens_only(self) -> None:
        import time

        store = AuthTokenStore(secret=b"sec-audit-tokenstate", ttl=3600)
        old = store.issue(AuthUser(id=9, username="u9", nickname="u9", role="student"))[0]
        self.assertIsNotNone(store.resolve(old))
        store.revoke_all_for_user(9)
        self.assertIsNone(store.resolve(old), "吊销后旧 token 必须失效")
        time.sleep(1.05)  # iat 秒级精度：跨过吊销秒后再签发新 token
        fresh = store.issue(AuthUser(id=9, username="u9", nickname="u9", role="student"))[0]
        self.assertIsNotNone(store.resolve(fresh), "重新登录签发的 token 必须可用")
        untouched = store.issue(AuthUser(id=10, username="u10", nickname="u10", role="student"))[0]
        self.assertIsNotNone(store.resolve(untouched), "不得波及其他用户")

    def test_disabled_user_rejected_and_check_is_cached(self) -> None:
        import app.db.session as db_session

        settings = Settings(
            llm_mode="fake",
            vector_mode="mock",
            graph_mode="mock",
            auth_required=True,
            dev_mode=False,
            mysql_url="mysql+pymysql://sec:pw@127.0.0.1:3306/db",
        )
        client, _ = build(settings)
        headers = {"Authorization": f"Bearer {token_for(77, 'victim', 'student')}"}
        calls = {"n": 0}

        def fake_new_session_active(url):
            calls["n"] += 1
            return _FakeSession(1)

        with unittest.mock.patch.object(db_session, "new_session", fake_new_session_active):
            ok = client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(calls["n"], 1)
        with unittest.mock.patch.object(db_session, "new_session", fake_new_session_active):
            ok2 = client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(ok2.status_code, 200)
        self.assertEqual(calls["n"], 1, "60s 缓存窗口内不应重复查库")

        self.auth_module._active_cache.clear()  # 模拟缓存过期
        with unittest.mock.patch.object(
            db_session, "new_session", lambda url: _FakeSession(0)
        ):
            denied = client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(denied.status_code, 401, denied.text)
        self.assertIn("禁用", denied.json()["detail"])

    def test_mock_mode_skips_active_check(self) -> None:
        client, _ = build(dev_settings())
        headers = {"Authorization": f"Bearer {token_for(5, 'stu5', 'student')}"}
        response = client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)


class UploadPathSafetyTests(unittest.TestCase):
    """文件名穿越：拒绝路径不得落盘，落盘名必须与用户输入无关。"""

    def setUp(self) -> None:
        self.client, _ = build(prod_settings())
        self.admin_headers = {
            "Authorization": f"Bearer {token_for(1, 'admin', 'admin')}"
        }

    def test_traversal_filename_rejected_and_no_file_written(self) -> None:
        for evil in ["../../evil.txt", "..\\..\\evil.txt", "/tmp/evil.md"]:
            response = self.client.post(
                "/api/v1/documents/upload",
                json={"filename": evil, "content": "第一条 测试"},
                headers=self.admin_headers,
            )
            # mock 向量模式下在入库预检即被拒（400）；无论哪种原因都不允许落盘
            self.assertEqual(response.status_code, 400, msg=f"{evil} 应被拒绝")
            self.assertNotIn(evil, response.text, "错误信息不得回显攻击路径")

        for leftover in [
            BACKEND_DIR / "evil.txt",
            BACKEND_DIR.parent / "evil.txt",
            Path("/tmp/evil.md"),
        ]:
            self.assertFalse(leftover.exists(), msg=f"不得产生文件: {leftover}")


if __name__ == "__main__":
    unittest.main()
