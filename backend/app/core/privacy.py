"""日志脱敏（M-31）。

约束：任何要写入日志的用户输入（提问原文、联系方式、失物描述）都必须先过这里。
当前代码没有主动打印这些字段，但没有统一入口时，后续新增日志很容易把 PII 带进去。

用法：
    logger.info("chat query=%s", mask_pii(state.query))
    logger.warning("repair contact=%s", mask_pii(contact))

不做"全量替换后再打印"的黑盒：函数本身只做截断 + 三类最常见的标识脱敏，
保持日志可读，避免排障时什么都看不到。
"""

from __future__ import annotations

import re

# 手机号：保留前 3 后 4
_MOBILE_RE = re.compile(r"\b(1[3-9]\d)\d{4}(\d{4})\b")
# 邮箱：保留首字符与域名
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# 身份证号：保留前 6 后 4
_ID_RE = re.compile(r"\b(\d{6})\d{8}(\d{3}[0-9Xx])\b")

DEFAULT_MAX_LENGTH = 60


def mask_pii(text: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """把一段用户输入处理成可安全写日志的形式。

    - 手机号 → 138****0000
    - 邮箱   → a***@example.com
    - 身份证 → 110101********1234
    - 超长内容按 max_length 截断并标注总长度（便于判断"用户输入很长"而不必看原文）
    """
    value = str(text or "")
    if not value:
        return ""

    masked = _MOBILE_RE.sub(r"\1****\2", value)
    masked = _EMAIL_RE.sub(r"\1***\2", masked)
    masked = _ID_RE.sub(r"\1********\2", masked)

    if len(masked) > max_length:
        return f"{masked[:max_length]}…(len={len(value)})"
    return masked
