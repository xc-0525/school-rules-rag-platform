"""认证与会话安全用例（对应清单 SEC-WEB-07 / SEC-WEB-08）。

覆盖：密码哈希加盐与定时安全比较、令牌防篡改、过期、登出吊销、
Bearer 解析边界、注册策略（保留用户名 / 弱口令 / 角色固定 student）、
登录失败统一文案（防用户枚举）。

运行：cd backend && python -m unittest tests.security.test_auth_session -v
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
from app.core.security import hash_password, verify_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.auth import (  # noqa: E402
    AuthTokenStore,
    AuthUser,
    _RESERVED_USERNAMES,
    resolve_token,
)
from app.services.container import build_services  # noqa: E402
from app.services.llm import FakeLLM  # noqa: E402

_FIXED_SECRET = b"sec-audit-fixed-secret-for-tests"


def dev_settings() -> Settings:
    """本地联调态：放开匿名，注册可用（注册接口本身无鉴权门槛）。"""
    return Settings(
        llm_mode="fake",
        vector_mode="mock",
        graph_mode="mock",
        auth_required=False,
        dev_mode=True,
    )


def make_client(settings: Settings) -> TestClient:
    services = build_services(settings)
    services.llm = FakeLLM()
    return TestClient(create_app(settings, services=services))


def make_user(user_id: int, username: str, role: str) -> AuthUser:
    return AuthUser(id=user_id, username=username, nickname=username, role=role)


class PasswordHashTests(unittest.TestCase):
    """SEC-WEB-07：口令存储。"""

    def test_same_password_two_hashes_differ(self) -> None:
        """随机盐生效：同一口令两次哈希结果必须不同。"""
        first = hash_password("S3cure-Pass!")
        second = hash_password("S3cure-Pass!")
        self.assertNotEqual(first, second)

    def test_stored_hash_never_contains_plaintext(self) -> None:
        stored = hash_password("S3cure-Pass!")
        self.assertNotIn("S3cure-Pass!", stored)

    def test_stored_hash_records_algorithm_and_iterations(self) -> None:
        stored = hash_password("S3cure-Pass!")
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
        self.assertEqual(algorithm, "pbkdf2_sha256")
        self.assertGreaterEqual(int(iterations), 100_000, "迭代次数不得低于 10 万")
        self.assertTrue(salt_b64 and digest_b64)

    def test_verify_accepts_correct_and_rejects_wrong(self) -> None:
        stored = hash_password("S3cure-Pass!")
        self.assertTrue(verify_password("S3cure-Pass!", stored))
        self.assertFalse(verify_password("s3cure-pass!", stored))
        self.assertFalse(verify_password("", stored))

    def test_verify_rejects_malformed_stored_values(self) -> None:
        """哈希串被篡改/来自旧算法/字段非法时必须返回 False，不允许抛异常路径。"""
        for bad in [
            "",
            "not-a-hash",
            "md5$1000$aa$bb",  # 算法名不匹配
            "pbkdf2_sha256$notanint$aa$bb",  # 迭代次数非法
            "pbkdf2_sha256$120000$!!!$bb",  # 盐非 base64
        ]:
            self.assertFalse(verify_password("anything", bad), msg=f"应拒绝: {bad!r}")


class TokenSecurityTests(unittest.TestCase):
    """SEC-WEB-08：签名令牌。"""

    def _store(self, ttl: int = 3600) -> AuthTokenStore:
        return AuthTokenStore(secret=_FIXED_SECRET, ttl=ttl)

    def test_roundtrip(self) -> None:
        store = self._store()
        token, ttl = store.issue(make_user(7, "alice", "student"))
        self.assertEqual(ttl, 3600)
        user = store.resolve(token)
        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.id, 7)
        self.assertEqual(user.role, "student")

    def test_tampered_payload_rejected(self) -> None:
        """改 payload 一个字节（如把 student 改 admin）必须失效。"""
        store = self._store()
        token = store.issue(make_user(7, "alice", "student"))[0]
        body, _, signature = token.partition(".")
        forged = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + signature
        self.assertIsNone(store.resolve(forged))

    def test_tampered_signature_rejected(self) -> None:
        store = self._store()
        token = store.issue(make_user(7, "alice", "student"))[0]
        body, _, signature = token.partition(".")
        bad_sig = ("A" if signature[0] != "A" else "B") + signature[1:]
        self.assertIsNone(store.resolve(f"{body}.{bad_sig}"))
        self.assertIsNone(store.resolve(f"{body}."))

    def test_token_without_signature_rejected(self) -> None:
        store = self._store()
        token = store.issue(make_user(7, "alice", "student"))[0]
        body = token.partition(".")[0]
        self.assertIsNone(store.resolve(body))

    def test_expired_token_rejected(self) -> None:
        store = self._store(ttl=-1)
        token = store.issue(make_user(7, "alice", "student"))[0]
        self.assertIsNone(store.resolve(token))

    def test_logout_revokes_token(self) -> None:
        store = self._store()
        token = store.issue(make_user(7, "alice", "student"))[0]
        self.assertIsNotNone(store.resolve(token))
        store.revoke(token)
        self.assertIsNone(store.resolve(token), "登出后旧 token 必须失效")

    def test_non_bearer_scheme_rejected(self) -> None:
        self.assertIsNone(resolve_token(None))
        self.assertIsNone(resolve_token(""))
        self.assertIsNone(resolve_token("Basic dXNlcjpwYXNz"))
        self.assertIsNone(resolve_token("Bearer "))  # 空凭据


class RegistrationPolicyTests(unittest.TestCase):
    """SEC-WEB-07：注册边界。"""

    def setUp(self) -> None:
        self.client = make_client(dev_settings())

    def test_register_rejects_reserved_username(self) -> None:
        """保留用户名 admin/student 不开放自助注册（防提权）。"""
        self.assertIn("admin", _RESERVED_USERNAMES)
        response = self.client.post(
            "/api/v1/auth/register",
            json={"username": "admin", "password": "pass1234"},
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_register_rejects_short_password(self) -> None:
        response = self.client.post(
            "/api/v1/auth/register",
            json={"username": "secstu01", "password": "12345"},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_register_rejects_html_in_username(self) -> None:
        """用户名白名单校验挡住 <script>（XSS 入口在注册层就关闭）。"""
        response = self.client.post(
            "/api/v1/auth/register",
            json={"username": "x<script>", "password": "pass1234"},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_registered_role_is_pinned_student(self) -> None:
        """注册接口不接受任何方式提权为 admin。"""
        response = self.client.post(
            "/api/v1/auth/register",
            json={"username": "secstu02", "password": "pass1234", "role": "admin"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["user"]["role"], "student")

        me = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {response.json()['token']}"},
        )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["role"], "student")

    def test_login_failure_message_is_uniform(self) -> None:
        """密码错误与用户不存在返回同一文案、同一状态码（防用户枚举）。"""
        created = self.client.post(
            "/api/v1/auth/register",
            json={"username": "secstu03", "password": "pass1234"},
        )
        self.assertEqual(created.status_code, 201)

        wrong_password = self.client.post(
            "/api/v1/auth/login",
            json={"username": "secstu03", "password": "wrong-pass"},
        )
        unknown_user = self.client.post(
            "/api/v1/auth/login",
            json={"username": "no_such_user", "password": "whatever1"},
        )
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_user.status_code, 401)
        self.assertEqual(wrong_password.json()["detail"], unknown_user.json()["detail"])
        self.assertNotIn("pass1234", wrong_password.text)


class ErrorEchoGuardTests(unittest.TestCase):
    """P0-2：服务故障时对外固定文案，原始异常（主机/账号/错误类型）只进日志。"""

    def setUp(self) -> None:
        self.client = make_client(dev_settings())

    def test_login_503_does_not_echo_mysql_error(self) -> None:
        import app.api.v1.auth as auth_api

        def boom(settings, username, password):  # noqa: ARG001
            raise RuntimeError(
                "Access denied for user 'root'@'10.0.0.5' (using password: YES)"
            )

        with unittest.mock.patch.object(auth_api, "authenticate", boom):
            response = self.client.post(
                "/api/v1/auth/login", json={"username": "secstu", "password": "x-pass"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "登录服务暂不可用，请稍后重试")
        for leak in ["root", "10.0.0.5", "RuntimeError", "Access denied"]:
            self.assertNotIn(leak, response.text, msg=f"响应泄露: {leak}")


class LoginLockoutTests(unittest.TestCase):
    """P1-1：按用户名全局失败锁定——换 IP 也绕不过；成功登录清零。"""

    def setUp(self) -> None:
        settings = Settings(
            llm_mode="fake",
            vector_mode="mock",
            graph_mode="mock",
            auth_required=True,
            dev_mode=False,
            rate_limit_per_minute=0,  # 关闭分钟限流，只验证失败锁定维度
            rate_limit_per_day=0,
            rate_limit_enabled=True,
        )
        services = build_services(settings)
        services.llm = FakeLLM()
        self.client = TestClient(create_app(settings, services=services))
        created = self.client.post(
            "/api/v1/auth/register",
            json={"username": "lockstu01", "password": "pass1234"},
        )
        # 409 = 同进程先前的测试类已注册过（_registered_users 模块级共享），口令一致可直接用
        self.assertIn(created.status_code, (201, 409), created.text)

    def _login(self, password: str):
        return self.client.post(
            "/api/v1/auth/login",
            json={"username": "lockstu01", "password": password},
        )

    def test_locks_after_threshold_even_with_correct_password(self) -> None:
        for _ in range(5):
            response = self._login("wrong-password")
            self.assertEqual(response.status_code, 401)
        # 第 6 次即使密码正确也拒绝：锁定按用户名全局生效
        blocked = self._login("pass1234")
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn("Retry-After", blocked.headers)
        self.assertNotIn("pass1234", blocked.text)

    def test_success_login_resets_failure_count(self) -> None:
        """成功登录清空失败计数：4 败→1 成→再 4 败仍不锁定。"""
        for _ in range(4):
            self.assertEqual(self._login("wrong-password").status_code, 401)
        ok = self._login("pass1234")
        self.assertEqual(ok.status_code, 200, ok.text)
        for _ in range(4):
            self.assertEqual(self._login("wrong-password").status_code, 401)
        still_ok = self._login("pass1234")
        self.assertEqual(still_ok.status_code, 200, "成功登录应清空失败计数")

    def test_other_usernames_unaffected(self) -> None:
        for _ in range(5):
            self.client.post(
                "/api/v1/auth/login",
                json={"username": "lockstu01", "password": "wrong-password"},
            )
        other = self.client.post(
            "/api/v1/auth/login",
            json={"username": "nobody404", "password": "whatever1"},
        )
        self.assertEqual(other.status_code, 401, "其他账号不得被连带锁定")
        self.assertEqual(other.json()["detail"], "用户名或密码错误")


if __name__ == "__main__":
    unittest.main()
