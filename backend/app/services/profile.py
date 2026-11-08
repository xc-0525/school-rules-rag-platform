"""用户身份画像（N-20）。

为什么需要：语料里同时存在本科与专科两套学籍规定（03/04），
不区分学历就检索，容易把专科条款答给本科生。

三层处理：
1. 数据：users.education_level（本人设置）/ documents.education_level（入库标注）；
2. 检索：按学历过滤召回结果，保留「通用」文档；
3. 生成：Prompt 注入身份说明，未设置时明确提示口径差异，不替用户猜。

内存模式提供进程内 profile 存储，便于本地演示与单测。
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.config import Settings

EDUCATION_LABELS: dict[str, str] = {
    "unset": "未设置",
    "undergraduate": "本科",
    "junior": "专科",
    "general": "通用",
}
# 用户可自行选择的值（general 只用于文档）
USER_EDUCATION_VALUES = ("unset", "undergraduate", "junior")

_mock_lock = threading.Lock()
_mock_profiles: dict[int, dict[str, Any]] = {}


class ProfileError(ValueError):
    """资料不合法，HTTP 层映射 400。"""


def normalize_education(value: str | None, *, allow_general: bool = False) -> str:
    value = (value or "").strip().lower()
    allowed = USER_EDUCATION_VALUES + (("general",) if allow_general else ())
    if value not in allowed:
        raise ProfileError(f"学历层次只能是 {' / '.join(allowed)}")
    return value


def education_label(value: str | None) -> str:
    return EDUCATION_LABELS.get(str(value or ""), "未设置")


def infer_education_level(text: str | None) -> str:
    """从标题/文件名判断适用对象：出现「专科」为专科，出现「本科」为本科，其余通用。"""
    content = str(text or "")
    if "专科" in content:
        return "junior"
    if "本科" in content:
        return "undergraduate"
    return "general"


def filter_evidence(items: list[Any], level: str | None, limit: int = 3) -> list[Any]:
    """按学历过滤召回：保留同层次与通用文档；未设置学历时不过滤。"""
    level = str(level or "unset")
    if level not in ("undergraduate", "junior"):
        return list(items)[:limit]
    kept = [
        item
        for item in items
        if infer_education_level(getattr(item, "title", "")) in (level, "general")
    ]
    return kept[:limit]


def profile_prompt_line(level: str | None) -> str:
    level = str(level or "unset")
    if level == "undergraduate":
        return (
            "【用户身份】当前用户为本科生。涉及学籍、培养方案、奖助等可能分本科/专科口径的问题，"
            "优先引用本科规定；若只能检索到专科规定，必须在答案中显式说明适用对象不同。"
        )
    if level == "junior":
        return (
            "【用户身份】当前用户为专科生。涉及学籍、培养方案、奖助等可能分本科/专科口径的问题，"
            "优先引用专科规定；若只能检索到本科规定，必须在答案中显式说明适用对象不同。"
        )
    return (
        "【用户身份】当前用户未设置学历层次。若问题涉及本科/专科口径差异，"
        "先说明两者可能不同，并提示用户在“个人资料”补全，不要替用户猜测。"
    )


def _empty_profile(user_id: int) -> dict[str, Any]:
    return {
        "user_id": int(user_id),
        "education_level": "unset",
        "education_label": EDUCATION_LABELS["unset"],
        "college": None,
        "grade": None,
    }


def _decorate(row: dict[str, Any], user_id: int) -> dict[str, Any]:
    profile = _empty_profile(user_id)
    profile.update(
        {
            "education_level": str(row.get("education_level") or "unset"),
            "college": row.get("college"),
            "grade": row.get("grade"),
        }
    )
    profile["education_label"] = education_label(profile["education_level"])
    return profile


def get_profile(settings: Settings, user_id: int) -> dict[str, Any]:
    """读取资料；无 MySQL 时用进程内存储，读不到返回 unset。"""
    if not settings.mysql_url:
        with _mock_lock:
            row = _mock_profiles.get(int(user_id))
        return _decorate(dict(row) if row else {}, user_id)

    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        user = session.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
        if user is None:
            return _empty_profile(user_id)
        return _decorate(
            {
                "education_level": user.education_level,
                "college": user.college,
                "grade": user.grade,
            },
            user_id,
        )


def update_profile(
    settings: Settings,
    user_id: int,
    *,
    education_level: str | None = None,
    college: str | None = None,
    grade: str | None = None,
) -> dict[str, Any]:
    """本人补全资料。None 表示不改；空串表示清空（学历不可清空，只能选 unset）。"""
    if education_level is not None:
        education_level = normalize_education(education_level)
    if college is not None:
        college = college.strip()
        if len(college) > 64:
            raise ProfileError("学院名称不能超过 64 个字符")
    if grade is not None:
        grade = grade.strip()
        if len(grade) > 16:
            raise ProfileError("年级不能超过 16 个字符")

    if not settings.mysql_url:
        with _mock_lock:
            row = dict(_mock_profiles.get(int(user_id)) or {})
            if education_level is not None:
                row["education_level"] = education_level
            if college is not None:
                row["college"] = college or None
            if grade is not None:
                row["grade"] = grade or None
            _mock_profiles[int(user_id)] = row
        return _decorate(row, user_id)

    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        user = session.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
        if user is None:
            raise ProfileError("用户不存在")
        if education_level is not None:
            user.education_level = education_level
        if college is not None:
            user.college = college or None
        if grade is not None:
            user.grade = grade or None
        session.commit()
        return _decorate(
            {
                "education_level": user.education_level,
                "college": user.college,
                "grade": user.grade,
            },
            user_id,
        )


def clear_mock_profiles() -> None:
    with _mock_lock:
        _mock_profiles.clear()
