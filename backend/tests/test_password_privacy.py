"""密码策略（M-19）与日志脱敏（M-31）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.password_policy import (  # noqa: E402
    MAX_LENGTH,
    MIN_LENGTH,
    PasswordPolicyError,
    validate_password,
)
from app.core.privacy import mask_pii  # noqa: E402


class PasswordPolicyTests(unittest.TestCase):
    def test_accepts_reasonable_passwords(self) -> None:
        for password in ("pass1234", "demo12345", "new-pass-456", "Aa1!aaaa", "wut2026#lib"):
            validate_password(password)  # 不抛异常即通过

    def test_rejects_short(self) -> None:
        with self.assertRaises(PasswordPolicyError) as ctx:
            validate_password("ab1")
        self.assertIn(str(MIN_LENGTH), str(ctx.exception))
        self.assertIn("位", str(ctx.exception))

    def test_rejects_too_long(self) -> None:
        with self.assertRaises(PasswordPolicyError):
            validate_password("a1" * (MAX_LENGTH // 2 + 1))

    def test_rejects_single_char_class(self) -> None:
        for password in ("12345678", "abcdefgh", "!!!!!!!!"):
            with self.assertRaises(PasswordPolicyError):
                validate_password(password)

    def test_rejects_common_weak(self) -> None:
        for password in ("password", "admin123", "qwerty123", "abc12345"):
            with self.assertRaises(PasswordPolicyError):
                validate_password(password)

    def test_rejects_low_entropy(self) -> None:
        # 只有两种字符且长度达到 8：aaaa1111 这类
        with self.assertRaises(PasswordPolicyError) as ctx:
            validate_password("aaaa1111")
        self.assertIn("简单", str(ctx.exception))

    def test_empty_rejected(self) -> None:
        with self.assertRaises(PasswordPolicyError):
            validate_password("")


class PrivacyMaskTests(unittest.TestCase):
    def test_plain_text_unchanged(self) -> None:
        self.assertEqual(mask_pii("宿舍晚上几点熄灯？"), "宿舍晚上几点熄灯？")

    def test_mobile_masked(self) -> None:
        self.assertEqual(mask_pii("我的手机 13812345678 打不通"), "我的手机 138****5678 打不通")

    def test_email_masked(self) -> None:
        self.assertEqual(mask_pii("zhangsan@example.com"), "z***@example.com")

    def test_id_card_masked(self) -> None:
        self.assertEqual(mask_pii("110101199001011234"), "110101********1234")

    def test_long_text_truncated_with_length(self) -> None:
        text = "很长的问题" * 30
        masked = mask_pii(text, max_length=20)
        self.assertTrue(masked.endswith(f"(len={len(text)})"))
        self.assertLess(len(masked), len(text))

    def test_empty_safe(self) -> None:
        self.assertEqual(mask_pii(""), "")
        self.assertEqual(mask_pii(None), "")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
