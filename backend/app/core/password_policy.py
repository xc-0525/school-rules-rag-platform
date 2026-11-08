"""密码强度策略（M-19）。

独立模块：学生注册、管理员重置密码共用同一套规则，避免"注册严、重置松"的绕过口子。
只校验**新设置**的密码，不强制存量用户立即改密（存量兼容）。

规则：
- 长度 8–128；
- 至少两类字符（字母 / 数字 / 符号，大小写算同一类）；
- 不在内置弱口令表内，且不是"12345678""aaaaaaaa"这类低熵串。

不引入外部服务或字典包：内置 40 条常见弱口令足够覆盖校园场景，
也避免离线部署时多一个依赖。
"""

from __future__ import annotations

MIN_LENGTH = 8
MAX_LENGTH = 128
REQUIRED_CLASSES = 2

# 常见弱口令（小写比较）
WEAK_PASSWORDS: frozenset[str] = frozenset(
    {
        "12345678",
        "123456789",
        "1234567890",
        "12345678910",
        "87654321",
        "11111111",
        "00000000",
        "88888888",
        "66666666",
        "password",
        "password1",
        "password123",
        "passw0rd",
        "p@ssw0rd",
        "qwertyui",
        "qwerty123",
        "qwertyuiop",
        "abc12345",
        "abcd1234",
        "a1234567",
        "aa123456",
        "admin123",
        "admin1234",
        "administrator",
        "iloveyou",
        "woaini1314",
        "woaini520",
        "qq123456",
        "1qaz2wsx",
        "1q2w3e4r",
        "zxcvbnm1",
        "letmein1",
        "welcome1",
        "monkey123",
        "dragon123",
        "sunshine1",
        "football1",
        "superman1",
        "student1",
        "teacher1",
    }
)


class PasswordPolicyError(ValueError):
    """密码不符合策略（调用方转 400）。"""


def _char_classes(password: str) -> int:
    """统计字符类别数：字母（不分大小写）/ 数字 / 符号。"""
    has_letter = any(char.isalpha() for char in password)
    has_digit = any(char.isdigit() for char in password)
    has_symbol = any(not char.isalnum() for char in password)
    return sum([has_letter, has_digit, has_symbol])


def validate_password(password: str) -> None:
    """校验新密码；不通过抛 PasswordPolicyError（消息可直接展示给用户）。"""
    value = password or ""
    if len(value) < MIN_LENGTH:
        raise PasswordPolicyError(f"密码至少 {MIN_LENGTH} 位")
    if len(value) > MAX_LENGTH:
        raise PasswordPolicyError(f"密码不能超过 {MAX_LENGTH} 位")
    if _char_classes(value) < REQUIRED_CLASSES:
        raise PasswordPolicyError("密码需包含字母、数字或符号中的至少两类")
    if value.lower() in WEAK_PASSWORDS:
        raise PasswordPolicyError("该密码过于常见，请换一个")
    # 低熵串：整串只有 1–2 种字符（aaaa1111、12121212 这类）
    if len(set(value)) <= 2:
        raise PasswordPolicyError("该密码过于简单，请增加不同的字符")
