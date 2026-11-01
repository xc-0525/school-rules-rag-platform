"""公告模块：时效性通知的读取、发布与管理。

设计要点（见 docs/platform-extension.md 第 4 节）：

- **有效期是第一等公民**：停水停电是时段信息，过期必须自动下架。
  列表/检索默认过滤 `expires_at < now()`，不依赖人工维护。
- **页面与问答同源**：`list_notices` 与 `search_notices` 共用同一套过滤条件，
  避免"页面看不到、问答却答得出来"的不一致。
- **双实现**：有 MYSQL_URL 走 announcements 表；否则进程内 dict（够本地演示与单测）。
  与 tickets.py 保持同一风格。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings

# 分类编码 → 中文名。前端与问答工具都按这张表展示。
NOTICE_CATEGORIES: dict[str, str] = {
    "water": "停水",
    "power": "停电",
    "network": "网络",
    "teaching": "教务",
    "life": "生活服务",
    "other": "其他",
}

DEFAULT_CATEGORY = "other"


def category_label(code: str | None) -> str:
    return NOTICE_CATEGORIES.get((code or "").strip().lower(), NOTICE_CATEGORIES[DEFAULT_CATEGORY])


# ---------------------------------------------------------------------------
# 内存实现（无 MYSQL_URL 时使用）
# ---------------------------------------------------------------------------
_mock_lock = threading.Lock()
_mock_seq = 0
_mock_notices: list[dict[str, Any]] = []


def _seed_mock() -> None:
    """首次访问时放两条示例，保证空库也能演示。"""
    if _mock_notices:
        return
    now = datetime.now()
    for index, (title, content, category, pinned) in enumerate(
        [
            (
                "关于图书馆延长开放时间的通知",
                "为方便同学复习，自本周起图书馆周一至周五开放时间延长至 22:30，周六周日 09:00-21:30。",
                "life",
                True,
            ),
            (
                "校园卡服务中心迁址公告",
                "校园卡服务中心自本周起迁至行政楼一层 105 室，办理挂失、补卡、充值业务，服务时间 08:30-17:00。",
                "life",
                False,
            ),
        ]
    ):
        global _mock_seq
        _mock_seq += 1
        _mock_notices.append(
            {
                "id": _mock_seq,
                "title": title,
                "content": content,
                "category": category,
                "is_pinned": pinned,
                "status": "published",
                "publisher": "admin",
                "user_id": 1,
                "effective_at": (now - timedelta(days=index)).strftime("%Y-%m-%d %H:%M:%S"),
                "expires_at": None,
                "created_at": (now - timedelta(days=index)).strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": None,
            }
        )


def clear_mock_notices() -> None:
    global _mock_seq
    with _mock_lock:
        _mock_notices.clear()
        _mock_seq = 0


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _fmt(value: Any) -> str | None:
    moment = _parse_dt(value)
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else None


def _is_expired(row: dict[str, Any], now: datetime | None = None) -> bool:
    expires = _parse_dt(row.get("expires_at"))
    return bool(expires and expires <= (now or datetime.now()))


def _decorate(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["category_label"] = category_label(item.get("category"))
    item["is_expired"] = _is_expired(item)
    item["is_pinned"] = bool(item.get("is_pinned"))
    for key in ("effective_at", "expires_at", "created_at", "updated_at"):
        if key in item:
            item[key] = _fmt(item[key])
    return item


def _matches(row: dict[str, Any], category: str, keyword: str) -> bool:
    if category and (row.get("category") or "").lower() != category.lower():
        return False
    if keyword:
        haystack = f"{row.get('title') or ''} {row.get('content') or ''}"
        if keyword.lower() not in haystack.lower():
            return False
    return True


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def list_notices(
    settings: Settings,
    *,
    category: str = "",
    keyword: str = "",
    include_expired: bool = False,
    include_draft: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """公告列表：置顶优先，其次按生效时间倒序。"""
    limit = max(1, min(int(limit or 50), 200))
    if not settings.mysql_url:
        return _list_mock(category, keyword, include_expired, include_draft, limit)

    from app.db.session import new_session
    from app.db.models import Announcement, User

    with new_session(settings.mysql_url) as session:
        query = session.query(Announcement, User.username).outerjoin(
            User, User.id == Announcement.user_id
        )
        if not include_draft:
            query = query.filter(Announcement.status == "published")
        if not include_expired:
            query = query.filter(
                (Announcement.expires_at.is_(None)) | (Announcement.expires_at > datetime.now())
            )
        if category:
            query = query.filter(Announcement.category == category)
        if keyword:
            like = f"%{keyword}%"
            query = query.filter(Announcement.title.like(like) | Announcement.content.like(like))
        rows = (
            query.order_by(
                Announcement.is_pinned.desc(),
                Announcement.effective_at.desc(),
                Announcement.id.desc(),
            )
            .limit(limit)
            .all()
        )
        result = []
        for notice, username in rows:
            result.append(
                _decorate(
                    {
                        "id": notice.id,
                        "title": notice.title,
                        "content": notice.content,
                        "category": notice.category,
                        "is_pinned": notice.is_pinned,
                        "status": notice.status,
                        "publisher": username or "",
                        "user_id": notice.user_id,
                        "effective_at": notice.effective_at,
                        "expires_at": notice.expires_at,
                        "created_at": notice.created_at,
                        "updated_at": notice.updated_at,
                    }
                )
            )
        return result


def _list_mock(
    category: str, keyword: str, include_expired: bool, include_draft: bool, limit: int
) -> list[dict[str, Any]]:
    with _mock_lock:
        _seed_mock()
        rows = [
            dict(row)
            for row in _mock_notices
            if (include_draft or row.get("status") == "published")
            and (include_expired or not _is_expired(row))
            and _matches(row, category, keyword)
        ]
    # effective_at 在内存实现里可能是 datetime（新建）或 str（种子），统一格式化再比较
    rows.sort(
        key=lambda row: (bool(row.get("is_pinned")), _fmt(row.get("effective_at")) or ""),
        reverse=True,
    )
    return [_decorate(row) for row in rows[:limit]]


def get_notice(settings: Settings, notice_id: int) -> dict[str, Any] | None:
    if not settings.mysql_url:
        with _mock_lock:
            _seed_mock()
            row = next((dict(item) for item in _mock_notices if item["id"] == notice_id), None)
        return _decorate(row) if row else None

    from app.db.models import Announcement, User
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        row = (
            session.query(Announcement, User.username)
            .outerjoin(User, User.id == Announcement.user_id)
            .filter(Announcement.id == notice_id)
            .first()
        )
        if row is None:
            return None
        notice, username = row
        return _decorate(
            {
                "id": notice.id,
                "title": notice.title,
                "content": notice.content,
                "category": notice.category,
                "is_pinned": notice.is_pinned,
                "status": notice.status,
                "publisher": username or "",
                "user_id": notice.user_id,
                "effective_at": notice.effective_at,
                "expires_at": notice.expires_at,
                "created_at": notice.created_at,
                "updated_at": notice.updated_at,
            }
        )


def create_notice(settings: Settings, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """发布公告。payload 由 Pydantic 校验过，这里只管落库。"""
    title = str(payload.get("title") or "").strip()[:200]
    content = str(payload.get("content") or "").strip()
    if not title or not content:
        raise ValueError("标题与正文不能为空")
    category = (str(payload.get("category") or DEFAULT_CATEGORY)).lower()
    if category not in NOTICE_CATEGORIES:
        category = DEFAULT_CATEGORY
    status = "published" if str(payload.get("status") or "published") == "published" else "draft"
    is_pinned = bool(payload.get("is_pinned"))
    effective_at = _parse_dt(payload.get("effective_at")) or datetime.now()
    expires_at = _parse_dt(payload.get("expires_at"))
    if expires_at and expires_at <= effective_at:
        raise ValueError("失效时间必须晚于生效时间")

    if not settings.mysql_url:
        global _mock_seq
        with _mock_lock:
            _seed_mock()
            _mock_seq += 1
            row = {
                "id": _mock_seq,
                "title": title,
                "content": content,
                "category": category,
                "is_pinned": is_pinned,
                "status": status,
                "publisher": "admin",
                "user_id": user_id,
                "effective_at": effective_at,
                "expires_at": expires_at,
                "created_at": datetime.now(),
                "updated_at": None,
            }
            _mock_notices.append(row)
            return _decorate(dict(row))

    from app.db.models import Announcement
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        notice = Announcement(
            user_id=user_id,
            title=title,
            content=content,
            category=category,
            is_pinned=1 if is_pinned else 0,
            status=status,
            effective_at=effective_at,
            expires_at=expires_at,
        )
        session.add(notice)
        session.commit()
        return {
            "id": int(notice.id),
            "title": title,
            "content": content,
            "category": category,
            "category_label": category_label(category),
            "is_pinned": is_pinned,
            "status": status,
            "publisher": "",
            "user_id": user_id,
            "effective_at": _fmt(effective_at),
            "expires_at": _fmt(expires_at),
            "created_at": _fmt(datetime.now()),
            "updated_at": None,
            "is_expired": _is_expired({"expires_at": expires_at}),
        }


def update_notice(
    settings: Settings, notice_id: int, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """编辑 / 上下架。只更新 payload 里显式给出的字段。"""
    fields = {
        key: payload[key]
        for key in ("title", "content", "category", "is_pinned", "status", "effective_at", "expires_at")
        if key in payload
    }

    if not settings.mysql_url:
        with _mock_lock:
            _seed_mock()
            row = next((item for item in _mock_notices if item["id"] == notice_id), None)
            if row is None:
                return None
            if "category" in fields:
                category = str(fields["category"]).lower()
                row["category"] = category if category in NOTICE_CATEGORIES else DEFAULT_CATEGORY
            for key in ("title", "content", "status"):
                if key in fields:
                    row[key] = str(fields[key]).strip()
            if "is_pinned" in fields:
                row["is_pinned"] = bool(fields["is_pinned"])
            if "effective_at" in fields:
                row["effective_at"] = _parse_dt(fields["effective_at"])
            if "expires_at" in fields:
                row["expires_at"] = _parse_dt(fields["expires_at"])
            row["updated_at"] = datetime.now()
            return _decorate(dict(row))

    from app.db.models import Announcement
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        notice = session.get(Announcement, notice_id)
        if notice is None:
            return None
        if "title" in fields:
            notice.title = str(fields["title"]).strip()[:200]
        if "content" in fields:
            notice.content = str(fields["content"]).strip()
        if "category" in fields:
            category = str(fields["category"]).lower()
            notice.category = category if category in NOTICE_CATEGORIES else DEFAULT_CATEGORY
        if "is_pinned" in fields:
            notice.is_pinned = 1 if fields["is_pinned"] else 0
        if "status" in fields:
            notice.status = "published" if fields["status"] == "published" else "draft"
        if "effective_at" in fields:
            notice.effective_at = _parse_dt(fields["effective_at"])
        if "expires_at" in fields:
            notice.expires_at = _parse_dt(fields["expires_at"])
        notice.updated_at = datetime.now()
        session.commit()
        return get_notice(settings, notice_id)


def delete_notice(settings: Settings, notice_id: int) -> bool:
    if not settings.mysql_url:
        global _mock_notices
        with _mock_lock:
            _seed_mock()
            before = len(_mock_notices)
            _mock_notices = [item for item in _mock_notices if item["id"] != notice_id]
            return len(_mock_notices) < before

    from app.db.models import Announcement
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        notice = session.get(Announcement, notice_id)
        if notice is None:
            return False
        session.delete(notice)
        session.commit()
        return True


def search_notices(settings: Settings, keyword: str = "", limit: int = 5) -> list[dict[str, Any]]:
    """给问答工具用：只返回已发布且未过期的公告。"""
    return list_notices(settings, keyword=keyword, limit=limit)
