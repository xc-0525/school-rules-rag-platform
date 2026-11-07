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

import re
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


if __name__ == "__main__":
    unittest.main()
