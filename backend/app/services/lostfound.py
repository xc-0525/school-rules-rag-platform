"""失物招领服务：先审核后发布，图片走独立上传接口。

审核流（docs/campus-modules.md 第 4 节）：

```
pending_review ──approve──▶ published ──resolve(发布人)──▶ resolved
       │                                                       
       └──reject(附理由)──▶ rejected            published ──remove(管理员)──▶ removed
```

- 学生提交后进入 pending_review，公列表看不到（只能看自己的）；
- 审批超时不做后台定时任务：管理端提供 `pending_older_than` 查询，
  前端把超过 `lostfound_review_timeout_hours` 的条目标红，接定时器/消息推送时
  直接复用同一查询（扩展点见文档 7 节）；
- 图片：先 `POST /lost-found/images` 上传拿到 URL，发布时提交 URL 列表。
  服务层只接受本系统上传目录内的相对 URL，防外链与路径穿越。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings

LOSTFOUND_KINDS: dict[str, str] = {"lost": "失物", "found": "招领"}

LOSTFOUND_TRANSITIONS: dict[str, set[str]] = {
    "pending_review": {"published", "rejected"},
    "published": {"resolved", "removed"},
    "rejected": set(),
    "resolved": set(),
    "removed": set(),
}
PUBLIC_STATUSES = ("published",)


class LostFoundRuleError(ValueError):
    """业务规则不满足（非法流转 / 图片越权），HTTP 层映射为 4xx。"""


# ---------------------------------------------------------------------------
# 内存实现
# ---------------------------------------------------------------------------
_mock_lock = threading.Lock()
_mock_seq = 0
_mock_items: list[dict[str, Any]] = []


def clear_mock_lostfound() -> None:
    global _mock_seq
    with _mock_lock:
        _mock_items.clear()
        _mock_seq = 0


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _fmt(value: Any) -> str | None:
    moment = _parse_dt(value)
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else None


def _decode_images(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return [str(u) for u in raw]
    try:
        parsed = json.loads(str(raw))
        return [str(u) for u in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _decorate(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["kind_label"] = LOSTFOUND_KINDS.get(item.get("kind", ""), item.get("kind"))
    item["images"] = _decode_images(item.pop("image_urls", None))
    for key in ("created_at", "updated_at", "reviewed_at"):
        if key in item:
            item[key] = _fmt(item[key])
    return item


def sanitize_image_urls(urls: Any, max_images: int) -> list[str]:
    """校验发布时提交的图片 URL：必须是本系统上传接口返回的相对路径。"""
    if not urls:
        return []
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        raise LostFoundRuleError("image_urls 应为字符串数组")
    cleaned: list[str] = []
    for url in urls:
        text = url.strip()
        if not text:
            continue
        if not text.startswith("/api/v1/lost-found/images/"):
            raise LostFoundRuleError(
                f"图片必须先通过上传接口获取（非法地址：{text[:80]}）"
            )
        if ".." in text or text.endswith(".php"):
            raise LostFoundRuleError("图片地址包含非法字符")
        if text not in cleaned:
            cleaned.append(text)
        if len(cleaned) > max_images:
            raise LostFoundRuleError(f"最多携带 {max_images} 张图片")
    return cleaned


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def validate_payload(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """清洗并校验发布参数；非法输入抛 LostFoundRuleError。

    草稿预检与正式提交共用本函数，保证两处口径一致。
    """
    kind = str(payload.get("kind") or "").strip().lower()
    title = str(payload.get("title") or "").strip()[:200]
    description = str(payload.get("description") or "").strip()[:4000]
    location = str(payload.get("location") or "").strip()[:200]
    contact = str(payload.get("contact") or "").strip()[:100]
    max_images = max(0, int(getattr(settings, "lostfound_max_images", 3)))

    if kind not in LOSTFOUND_KINDS:
        raise LostFoundRuleError("kind 必须是 lost（我丢了东西）或 found（我捡到东西）")
    if not title or len(title) < 3:
        raise LostFoundRuleError("标题至少 3 个字")
    if len(description) < 10:
        raise LostFoundRuleError("描述至少 10 个字，帮助管理员判断信息真实性")
    if not contact:
        raise LostFoundRuleError("联系方式不能为空")

    return {
        "kind": kind,
        "title": title,
        "description": description,
        "location": location,
        "contact": contact,
        "images": sanitize_image_urls(payload.get("image_urls"), max_images),
    }


def create_item(
    settings: Settings,
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """提交发布申请 → pending_review，等待管理员审批。"""
    data = validate_payload(settings, payload)
    kind = data["kind"]
    title = data["title"]
    description = data["description"]
    location = data["location"]
    contact = data["contact"]
    images = data["images"]

    if not settings.mysql_url:
        global _mock_seq
        with _mock_lock:
            _mock_seq += 1
            row = {
                "id": _mock_seq,
                "user_id": user_id,
                "kind": kind,
                "title": title,
                "description": description,
                "location": location,
                "contact": contact,
                "image_urls": json.dumps(images, ensure_ascii=False) if images else None,
                "status": "pending_review",
                "reviewer_id": None,
                "review_remark": None,
                "reviewed_at": None,
                "created_at": datetime.now(),
                "updated_at": None,
            }
            _mock_items.append(row)
            return _decorate(dict(row))

    from app.db.models import LostFoundItem
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        item = LostFoundItem(
            user_id=user_id,
            kind=kind,
            title=title,
            description=description,
            location=location,
            contact=contact,
            image_urls=json.dumps(images, ensure_ascii=False) if images else None,
            status="pending_review",
            created_at=datetime.now(),
        )
        session.add(item)
        session.commit()
        return get_item(settings, int(item.id), user_id, is_admin=True)  # type: ignore[return-value]


def list_items(
    settings: Settings,
    *,
    user_id: int,
    is_admin: bool,
    kind: str = "",
    scope: str = "public",
    older_than_hours: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """scope：public=已发布（学生可见）/ mine=我的（含待审与驳回理由）/ pending=待审（管理员）。"""
    limit = max(1, min(int(limit or 50), 200))
    if not settings.mysql_url:
        with _mock_lock:
            rows = [
                dict(r)
                for r in _mock_items
                if _match_scope(r, user_id, is_admin, scope, kind, older_than_hours)
            ]
        rows.sort(key=lambda r: (_parse_dt(r["created_at"]) or datetime.min, r["id"]), reverse=True)
        return [_decorate(r) for r in rows[:limit]]

    from app.db.models import LostFoundItem
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        query = session.query(LostFoundItem)
        if scope == "mine":
            query = query.filter(LostFoundItem.user_id == user_id)
        elif scope == "pending":
            query = query.filter(LostFoundItem.status == "pending_review")
            if older_than_hours > 0:
                cutoff = datetime.now() - timedelta(hours=older_than_hours)
                query = query.filter(LostFoundItem.created_at <= cutoff)
        else:
            query = query.filter(LostFoundItem.status.in_(PUBLIC_STATUSES))
        if kind:
            query = query.filter(LostFoundItem.kind == kind)
        rows = (
            query.order_by(LostFoundItem.created_at.desc(), LostFoundItem.id.desc())
            .limit(limit)
            .all()
        )
        return [
            _decorate(
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "kind": r.kind,
                    "title": r.title,
                    "description": r.description,
                    "location": r.location,
                    "contact": r.contact,
                    "image_urls": r.image_urls,
                    "status": r.status,
                    "reviewer_id": r.reviewer_id,
                    "review_remark": r.review_remark,
                    "reviewed_at": r.reviewed_at,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
            )
            for r in rows
        ]


def _match_scope(
    row: dict[str, Any],
    user_id: int,
    is_admin: bool,
    scope: str,
    kind: str,
    older_than_hours: int,
) -> bool:
    if scope == "mine":
        ok = row["user_id"] == user_id
    elif scope == "pending":
        ok = row["status"] == "pending_review"
        if ok and older_than_hours > 0:
            cutoff = datetime.now() - timedelta(hours=older_than_hours)
            ok = (_parse_dt(row["created_at"]) or datetime.min) <= cutoff
    else:
        ok = row["status"] in PUBLIC_STATUSES
    if kind:
        ok = ok and row["kind"] == kind
    return ok


def get_item(
    settings: Settings,
    item_id: int,
    user_id: int,
    is_admin: bool,
) -> dict[str, Any] | None:
    """详情：已发布条目登录即可看；未发布的只有本人与管理员可见。"""
    if not settings.mysql_url:
        with _mock_lock:
            row = next((dict(r) for r in _mock_items if r["id"] == item_id), None)
        if row is None:
            return None
        if row["status"] not in PUBLIC_STATUSES and not is_admin and row["user_id"] != user_id:
            return None
        return _decorate(row)

    from app.db.models import LostFoundItem
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(LostFoundItem, item_id)
        if r is None:
            return None
        if r.status not in PUBLIC_STATUSES and not is_admin and r.user_id != user_id:
            return None
        return _decorate(
            {
                "id": r.id,
                "user_id": r.user_id,
                "kind": r.kind,
                "title": r.title,
                "description": r.description,
                "location": r.location,
                "contact": r.contact,
                "image_urls": r.image_urls,
                "status": r.status,
                "reviewer_id": r.reviewer_id,
                "review_remark": r.review_remark,
                "reviewed_at": r.reviewed_at,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
        )


def review_item(
    settings: Settings,
    item_id: int,
    reviewer_id: int,
    *,
    action: str,
    remark: str = "",
) -> dict[str, Any]:
    """管理员审批：approve → published；reject → rejected（必须附理由）。"""
    if action not in ("approve", "reject", "remove"):
        raise LostFoundRuleError(f"不支持的操作：{action}")
    if action == "reject" and not remark.strip():
        raise LostFoundRuleError("驳回必须填写理由，方便发布人修改后重新提交")

    target = {"approve": "published", "reject": "rejected", "remove": "removed"}[action]
    now = datetime.now()
    if not settings.mysql_url:
        with _mock_lock:
            row = next((r for r in _mock_items if r["id"] == item_id), None)
            if row is None:
                return None  # type: ignore[return-value]
            if row["status"] not in LOSTFOUND_TRANSITIONS or target not in LOSTFOUND_TRANSITIONS[row["status"]]:
                raise LostFoundRuleError(
                    f"当前状态为 {row['status']}，不能执行 {action}"
                )
            row["status"] = target
            row["reviewer_id"] = reviewer_id
            row["review_remark"] = remark.strip()[:4000] if remark.strip() else row["review_remark"]
            row["reviewed_at"] = now
            row["updated_at"] = now
            return _decorate(dict(row))

    from app.db.models import LostFoundItem
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(LostFoundItem, item_id)
        if r is None:
            return None
        if target not in LOSTFOUND_TRANSITIONS.get(r.status, set()):
            raise LostFoundRuleError(f"当前状态为 {r.status}，不能执行 {action}")
        r.status = target
        r.reviewer_id = reviewer_id
        if remark.strip():
            r.review_remark = remark.strip()[:4000]
        r.reviewed_at = now
        r.updated_at = now
        session.commit()
        return get_item(settings, item_id, reviewer_id, is_admin=True)  # type: ignore[return-value]


def resolve_item(
    settings: Settings,
    item_id: int,
    actor_id: int,
    *,
    is_admin: bool,
) -> dict[str, Any]:
    """发布人确认找回/归还 → resolved（终态）。管理员也可代操作。"""
    now = datetime.now()
    if not settings.mysql_url:
        with _mock_lock:
            row = next((r for r in _mock_items if r["id"] == item_id), None)
            if row is None or (not is_admin and row["user_id"] != actor_id):
                return None  # type: ignore[return-value]
            if row["status"] != "published":
                raise LostFoundRuleError(f"当前状态为 {row['status']}，只有已发布的条目可确认解决")
            row["status"] = "resolved"
            row["updated_at"] = now
            return _decorate(dict(row))

    from app.db.models import LostFoundItem
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(LostFoundItem, item_id)
        if r is None or (not is_admin and r.user_id != actor_id):
            return None
        if r.status != "published":
            raise LostFoundRuleError(f"当前状态为 {r.status}，只有已发布的条目可确认解决")
        r.status = "resolved"
        r.updated_at = now
        session.commit()
        return get_item(settings, item_id, actor_id, is_admin=True)  # type: ignore[return-value]
