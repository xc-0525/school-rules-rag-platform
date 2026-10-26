"""密码哈希（PBKDF2-SHA256，标准库实现，无第三方依赖）。

之所以不用 bcrypt：本机 Python 没有 bcrypt/passlib，而沙箱内 pip 安装受限；
PBKDF2-SHA256 是标准库能力、安全性足够（12 万次迭代 + 16 字节随机盐）。
后续可平滑替换为 bcrypt（只要保持 verify_password 的接口）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 120_000
_SALT_BYTES = 16


def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    """生成 `pbkdf2_sha256$迭代次数$盐(base64)$哈希(base64)` 格式的密码串。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "{}${}${}${}".format(
        _ALGORITHM,
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码；格式不正确或哈希不匹配都返回 False。"""
    try:
        algorithm, iterations, salt_b64, hash_b64 = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
