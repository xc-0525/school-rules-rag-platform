"""用户账号管理接口测试（仅管理员）。

MySQL 不可用时整组跳过（与 test_mysql_persistence 同口径）。
运行：cd backend && python -m unittest tests.test_admin_users -v

注意 token 吊销的语义：`iat <= cutoff` 一律拒绝（同秒签发的也拒），
所以"改密后重新登录"这类断言必须跨秒，否则新 token 也会被判失效。
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
from app.core.security import hash_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.auth import AuthUser, auth_tokens  # noqa: E402
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402

MYSQL_URL = os.getenv(
    "MYSQL_URL", "mysql+pymysql://root:@127.0.0.1:3306/school_rules_rag?charset=utf8mb4"
)
PREFIX = "uitest_"
ADMIN_NAME = f"{PREFIX}admin"
STUDENT_NAME = f"{PREFIX}stu"
BASE_PASSWORD = "uitest-pass-123"


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


def _connect():
    import pymysql

    return pymysql.connect(
        host="127.0.0.1", port=3306, user="root", password="", charset="utf8mb4",
        database="school_rules_rag", autocommit=True,
    )


def cleanup_test_users() -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE username LIKE %s", (f"{PREFIX}%",))


def create_user(username: str, role: str, status: int = 1) -> int:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password, nickname, role, status, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, NOW())",
                (username, hash_password(BASE_PASSWORD), username, role, status),
            )
            return int(cursor.lastrowid)


def user_row(user_id: int) -> dict:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, nickname, role, status FROM users WHERE id = %s", (user_id,)
            )
            row = cursor.fetchone()
            return {
                "id": row[0], "username": row[1], "nickname": row[2],
                "role": row[3], "status": int(row[4]),
            }


def token_for(user_id: int, username: str, role: str) -> str:
    token, _ = auth_tokens.issue(
        AuthUser(id=user_id, username=username, nickname=username, role=role)
    )
    return token


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@unittest.skipUnless(mysql_available(), "本机 MySQL / school_rules_rag 不可用，跳过集成测试")
class AdminUsersApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cleanup_test_users()

    @classmethod
    def tearDownClass(cls) -> None:
        cleanup_test_users()

    def setUp(self) -> None:
        cleanup_test_users()
        self.admin_id = create_user(ADMIN_NAME, "admin")
        self.student_id = create_user(STUDENT_NAME, "student")
        self.settings = Settings(
            llm_mode="fake",
            mysql_url=MYSQL_URL,
            auth_required=True,
            dev_mode=False,
        )
        services = build_services(self.settings)
        services.llm = FakeLLM()
        self.client = TestClient(create_app(self.settings, services=services))
        self.admin_headers = headers(token_for(self.admin_id, ADMIN_NAME, "admin"))
        self.student_headers = headers(token_for(self.student_id, STUDENT_NAME, "student"))

    # ---------------- 鉴权 ----------------
    def test_student_forbidden(self) -> None:
        response = self.client.get("/api/v1/admin/users", headers=self.student_headers)
        self.assertEqual(response.status_code, 403, response.text)

    def test_anonymous_rejected(self) -> None:
        response = self.client.get("/api/v1/admin/users")
        self.assertEqual(response.status_code, 401, response.text)

    # ---------------- 列表 ----------------
    def test_list_users_paginates_and_hides_password(self) -> None:
        response = self.client.get(
            "/api/v1/admin/users", headers=self.admin_headers, params={"limit": 1, "offset": 0}
        )
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertIn("has_more", page)
        self.assertEqual(len(page["items"]), 1)
        self.assertGreaterEqual(page["total"], 2)
        self.assertNotIn("password", page["items"][0])

    def test_list_users_keyword_and_role_filter(self) -> None:
        response = self.client.get(
            "/api/v1/admin/users",
            headers=self.admin_headers,
            params={"keyword": PREFIX, "role": "admin"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["items"]
        self.assertTrue(items)
        self.assertTrue(all(item["role"] == "admin" for item in items))
        self.assertTrue(all(PREFIX in item["username"] for item in items))

    def test_list_users_status_filter(self) -> None:
        response = self.client.get(
            "/api/v1/admin/users", headers=self.admin_headers, params={"status": 0, "keyword": PREFIX}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])

    def test_offset_beyond_total_returns_empty(self) -> None:
        response = self.client.get(
            "/api/v1/admin/users", headers=self.admin_headers, params={"offset": 9999}
        )
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertEqual(page["items"], [])
        self.assertFalse(page["has_more"])

    # ---------------- 修改 ----------------
    def test_update_nickname_and_promote(self) -> None:
        response = self.client.patch(
            f"/api/v1/admin/users/{self.student_id}",
            headers=self.admin_headers,
            json={"nickname": "改过的昵称", "role": "admin"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        row = user_row(self.student_id)
        self.assertEqual(row["nickname"], "改过的昵称")
        self.assertEqual(row["role"], "admin")

    def test_cannot_change_own_privilege(self) -> None:
        response = self.client.patch(
            f"/api/v1/admin/users/{self.admin_id}",
            headers=self.admin_headers,
            json={"role": "student"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("自己", response.json()["detail"])
        self.assertEqual(user_row(self.admin_id)["role"], "admin")

    def test_can_change_own_nickname(self) -> None:
        response = self.client.patch(
            f"/api/v1/admin/users/{self.admin_id}",
            headers=self.admin_headers,
            json={"nickname": "管理员新名"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(user_row(self.admin_id)["nickname"], "管理员新名")

    def test_invalid_role_rejected(self) -> None:
        response = self.client.patch(
            f"/api/v1/admin/users/{self.student_id}",
            headers=self.admin_headers,
            json={"role": "root"},
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_unknown_user_returns_404(self) -> None:
        response = self.client.patch(
            "/api/v1/admin/users/99999999", headers=self.admin_headers, json={"nickname": "x"}
        )
        self.assertEqual(response.status_code, 404, response.text)

    # ---------------- 状态与令牌 ----------------
    def test_disable_user_blocks_login_and_revokes_token(self) -> None:
        login = self.client.post(
            "/api/v1/auth/login", json={"username": STUDENT_NAME, "password": BASE_PASSWORD}
        )
        self.assertEqual(login.status_code, 200, login.text)

        response = self.client.patch(
            f"/api/v1/admin/users/{self.student_id}",
            headers=self.admin_headers,
            json={"status": 0},
        )
        self.assertEqual(response.status_code, 200, response.text)

        # 旧 token 立即失效（不等活跃状态缓存过期）
        blocked = self.client.get("/api/v1/auth/me", headers=self.student_headers)
        self.assertEqual(blocked.status_code, 401, blocked.text)

        # 禁用后无法再登录
        retry = self.client.post(
            "/api/v1/auth/login", json={"username": STUDENT_NAME, "password": BASE_PASSWORD}
        )
        self.assertEqual(retry.status_code, 401, retry.text)

        # 重新启用后可以登录
        self.client.patch(
            f"/api/v1/admin/users/{self.student_id}",
            headers=self.admin_headers,
            json={"status": 1},
        )
        again = self.client.post(
            "/api/v1/auth/login", json={"username": STUDENT_NAME, "password": BASE_PASSWORD}
        )
        self.assertEqual(again.status_code, 200, again.text)

    # ---------------- 重置密码 ----------------
    def test_reset_password_revokes_old_token_and_allows_new_password(self) -> None:
        new_password = "new-pass-456"
        response = self.client.post(
            f"/api/v1/admin/users/{self.student_id}/reset-password",
            headers=self.admin_headers,
            json={"new_password": new_password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["tokens_revoked"])

        old_token = self.client.get("/api/v1/auth/me", headers=self.student_headers)
        self.assertEqual(old_token.status_code, 401, old_token.text)

        old_password_login = self.client.post(
            "/api/v1/auth/login", json={"username": STUDENT_NAME, "password": BASE_PASSWORD}
        )
        self.assertEqual(old_password_login.status_code, 401, old_password_login.text)

        # 改密后立即用新密码登录必须可用：签发侧把 iat 抬到吊销截止之后，
        # 否则同一秒签发的新 token 会被自己的吊销规则判死（用户会被踢回登录页）
        new_login = self.client.post(
            "/api/v1/auth/login", json={"username": STUDENT_NAME, "password": new_password}
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)
        fresh_token = new_login.json()["token"]
        me = self.client.get("/api/v1/auth/me", headers=headers(fresh_token))
        self.assertEqual(me.status_code, 200, me.text)

    def test_short_password_rejected(self) -> None:
        response = self.client.post(
            f"/api/v1/admin/users/{self.student_id}/reset-password",
            headers=self.admin_headers,
            json={"new_password": "123"},
        )
        self.assertEqual(response.status_code, 400, response.text)

    # ---------------- Mock 模式 ----------------
    def test_mock_mode_rejects_user_admin(self) -> None:
        settings = Settings(llm_mode="fake", mysql_url="", auth_required=False, dev_mode=True)
        services = build_services(settings)
        services.llm = FakeLLM()
        client = TestClient(create_app(settings, services=services))
        response = client.get("/api/v1/admin/users")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("数据库", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
