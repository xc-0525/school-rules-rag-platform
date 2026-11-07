"""静态安全守卫（回归防线，对应清单各节"本仓库关联"）。

思路：关键防护点一旦被误删/绕过，测试立刻失败——
- 不得出现危险执行原语（eval/exec/命令执行/反序列化）；
- 不得出现字符串拼接 SQL（f-string 进 execute/text）；
- 不得出现服务端外发请求原语（SSRF 攻击面保持为零）；
- 目录穿越防御、定时安全比较、验签先于解析、Markdown 关闭 raw HTML、
  CORS 非通配、上传白名单等关键行必须仍在。

运行：cd backend && python -m unittest tests.security.test_static_guards -v
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"
FRONTEND_SRC = BACKEND_DIR.parent / "frontend" / "src"

# 危险执行原语：一个都不允许出现（新增必须走评审并在 ALLOWED_EXCEPTIONS 登记）
DANGEROUS_PATTERNS = re.compile(
    r"\beval\(|"
    r"\bexec\(|"
    r"os\.system\(|"
    r"subprocess\.(?:run|call|check_call|check_output|Popen)\(|"
    r"pickle\.loads?\(|"
    r"\byaml\.load\("
)

# 服务端外发请求原语：本项目当前无 SSRF 攻击面，引入即失败
OUTBOUND_PATTERNS = re.compile(
    r"requests\.(?:get|post|put|delete|request|Session)\(|"
    r"urllib\.request\.urlopen\(|"
    r"httpx\.(?:get|post|request|stream|Client|AsyncClient)\(|"
    r"socket\.create_connection\("
)

# 字符串拼接 SQL：f-string 进 execute()/text()
FSTRING_SQL = re.compile(r"""(?:execute|text)\(\s*f["']""")


def iter_app_sources() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class StaticGuardTests(unittest.TestCase):

    def test_no_dangerous_execution_primitives(self) -> None:
        offenders: list[str] = []
        for path in iter_app_sources():
            for lineno, line in enumerate(read_text(path).splitlines(), start=1):
                if DANGEROUS_PATTERNS.search(line):
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "发现危险执行原语：\n" + "\n".join(offenders))

    def test_no_outbound_http_primitives(self) -> None:
        """SSRF 攻击面保持为零：服务端代码不得按用户输入发起外部请求。"""
        offenders: list[str] = []
        for path in iter_app_sources():
            for lineno, line in enumerate(read_text(path).splitlines(), start=1):
                if OUTBOUND_PATTERNS.search(line):
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "发现服务端外发请求原语：\n" + "\n".join(offenders))

    def test_no_fstring_sql(self) -> None:
        offenders: list[str] = []
        for path in iter_app_sources():
            for lineno, line in enumerate(read_text(path).splitlines(), start=1):
                if FSTRING_SQL.search(line):
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "发现 f-string 拼 SQL：\n" + "\n".join(offenders))

    # ------------------------------------------------ 关键防护点锚定

    def test_preview_path_traversal_guard_anchored(self) -> None:
        """预览接口的 resolve + is_relative_to 双重校验不得被移除。"""
        source = read_text(BACKEND_DIR / "app" / "api" / "v1" / "documents.py")
        self.assertIn("allowed_roots", source)
        self.assertIn("resolved.is_relative_to(root)", source)
        self.assertIn('path.resolve()', source)
        self.assertIn("X-Content-Type-Options", source, "预览响应必须带 nosniff")

    def test_upload_whitelist_anchored(self) -> None:
        source = read_text(BACKEND_DIR / "app" / "api" / "v1" / "documents.py")
        self.assertIn('ALLOWED_SUFFIXES = {".md", ".txt", ".pdf"}', source)
        self.assertIn("uuid.uuid4().hex", source, "落盘名必须随机化，不得采用用户文件名")

    def test_password_compare_is_constant_time(self) -> None:
        source = read_text(BACKEND_DIR / "app" / "core" / "security.py")
        self.assertIn("hmac.compare_digest(actual, expected)", source)

    def test_token_verifies_signature_before_parsing(self) -> None:
        """先验签再解析 payload：顺序错了就是对不可信数据做解析。"""
        source = read_text(BACKEND_DIR / "app" / "services" / "auth.py")
        self.assertIn("hmac.compare_digest(signature, _sign(body, self._secret))", source)
        # compare_digest 必须出现在 resolve 的 json.loads 之前
        resolve_block = source[source.index("def resolve(") : source.index("def revoke(")]
        self.assertLess(
            resolve_block.index("compare_digest"),
            resolve_block.index("json.loads"),
            "resolve 必须先验签、后解析 JSON",
        )

    def test_markdown_renderer_disables_raw_html(self) -> None:
        """前端唯一 v-html 的渲染器必须关闭 raw HTML 且外链加 noopener。"""
        path = FRONTEND_SRC / "utils" / "markdown.ts"
        if not path.is_file():
            self.skipTest("frontend 源码不存在，跳过前端守卫")
        source = read_text(path)
        self.assertIn("html: false", source, "markdown-it 必须关闭原始 HTML")
        self.assertIn("noopener noreferrer", source)

    def test_cors_never_wildcard_with_credentials(self) -> None:
        source = read_text(BACKEND_DIR / "app" / "main.py")
        match = re.search(r"allow_origins\s*=\s*\[(.*?)\]", source, re.DOTALL)
        self.assertIsNotNone(match, "CORSMiddleware 配置缺失")
        origins = match.group(1) if match else ""
        self.assertNotIn('"*"', origins, "allow_credentials=True 时不得使用通配 Origin")
        self.assertNotIn("'*'", origins)

    def test_config_rejects_ambiguous_auth_combinations(self) -> None:
        """配置矛盾组合必须显式报错，不允许静默放行。"""
        from app.core.config import Settings

        with self.assertRaises(ValueError):
            Settings(auth_required=True, dev_mode=True)  # 自相矛盾
        with self.assertRaises(ValueError):
            Settings(auth_required=False, dev_mode=False)  # 含糊状态


class PlaceholderSecretGuardTests(unittest.TestCase):
    """P0-1：占位符/弱密钥必须在启动时被拒绝（子进程验证，不影响本进程导入）。"""

    def _import_auth(self, secret_env: str | None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("AUTH_TOKEN_SECRET", None)
        if secret_env is not None:
            env["AUTH_TOKEN_SECRET"] = secret_env
        return subprocess.run(
            [sys.executable, "-c", "import app.services.auth"],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_placeholder_secret_rejected(self) -> None:
        result = self._import_auth("CHANGE_ME_RANDOM_32_BYTES")
        self.assertNotEqual(result.returncode, 0, "占位符密钥必须拒绝启动")
        self.assertIn("配置不安全", result.stderr + result.stdout)

    def test_short_secret_rejected(self) -> None:
        result = self._import_auth("abc")
        self.assertNotEqual(result.returncode, 0, "短密钥必须拒绝启动")

    def test_valid_or_absent_secret_imports_fine(self) -> None:
        ok = self._import_auth("x" * 40)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        absent = self._import_auth(None)
        self.assertEqual(absent.returncode, 0, absent.stderr)


class SeedUsersGuardTests(unittest.TestCase):
    """P0-4：仓库不内嵌口令哈希；seed 脚本拒绝弱口令。"""

    def test_init_sql_has_no_embedded_password_hash(self) -> None:
        sql_path = BACKEND_DIR.parent / "sql" / "init_mysql.sql"
        if not sql_path.is_file():
            self.skipTest("sql/init_mysql.sql 不存在")
        content = read_text(sql_path)
        self.assertNotIn("pbkdf2_sha256$", content, "init_mysql.sql 不得内嵌口令哈希")
        self.assertNotIn("admin123", content)
        self.assertNotIn("student123", content)

    def test_seed_script_rejects_weak_passwords(self) -> None:
        import importlib.util

        script = BACKEND_DIR / "scripts" / "seed_users.py"
        if not script.is_file():
            self.skipTest("scripts/seed_users.py 不存在")
        spec = importlib.util.spec_from_file_location("seed_users", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for weak in ["admin123", "student123", "123456", "password"]:
            with self.assertRaises(SystemExit, msg=f"应拒绝 {weak}"):
                module.validate_password(weak, "INIT_ADMIN_PASSWORD")
        with self.assertRaises(SystemExit, msg="应拒绝过短口令"):
            module.validate_password("Ab1!", "INIT_ADMIN_PASSWORD")
        self.assertEqual(
            module.validate_password("S3cure-Pass-2026!", "INIT_ADMIN_PASSWORD"),
            "S3cure-Pass-2026!",
        )

    def test_entrypoint_wires_seed_before_serving(self) -> None:
        entry = read_text(BACKEND_DIR / "scripts" / "docker_entrypoint.py")
        self.assertIn("seed_users()", entry, "entrypoint 必须在启动服务前执行 seed")
        self.assertIn("seed_users.py", entry)


class PublicSurfaceTests(unittest.TestCase):
    """P1-3：生产档位不暴露交互式文档；/health 不含部署敏感字段；dev 档位不受影响。"""

    def _client(self, auth_required: bool):
        from fastapi.testclient import TestClient

        from app.core.config import Settings
        from app.main import create_app
        from app.services.container import build_services
        from app.services.llm import FakeLLM

        settings = Settings(
            llm_mode="fake",
            vector_mode="mock",
            graph_mode="mock",
            auth_required=auth_required,
            dev_mode=not auth_required,
        )
        services = build_services(settings)
        services.llm = FakeLLM()
        return TestClient(create_app(settings, services=services))

    def test_prod_hides_docs_and_openapi(self) -> None:
        client = self._client(auth_required=True)
        for path in ["/docs", "/redoc", "/openapi.json"]:
            response = client.get(path)
            self.assertEqual(response.status_code, 404, msg=f"{path} 生产档位应 404")

    def test_prod_health_has_no_sensitive_fields(self) -> None:
        client = self._client(auth_required=True)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        # 前端 App.vue 依赖字段必须保留
        self.assertIn("vector", body["modes"])
        self.assertIn("graph", body["modes"])
        self.assertIn("vector_ready", body)
        # 部署敏感字段不得出现在生产 /health
        for leak in ["dev_mode", "token", "rate_limit", "registration", "llm_configured"]:
            self.assertNotIn(leak, body, msg=f"/health 泄露部署信息: {leak}")
        self.assertNotIn("auth", body["modes"], "/health 泄露鉴权档位")

    def test_dev_still_has_docs(self) -> None:
        client = self._client(auth_required=False)
        self.assertEqual(client.get("/docs").status_code, 200)


if __name__ == "__main__":
    unittest.main()
