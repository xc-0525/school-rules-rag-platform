"""反馈与未命中工作台（N-25）。

两个视角：
- 反馈：点踩/点赞的归因与处理闭环 pending → attributed → resolved → verified；
- 未命中：拒答问题的归一化聚合（按 sha1 分组），支持标记 ignored / resolved。

数据全部来自现有表（feedback / messages / users），只新增一张 `unmatched_marks`
保存处理标记；不改 messages 结构。

MySQL 5.5 兼容：不使用窗口函数，聚合在应用层完成（数据量小），
「同一会话中该回答之前的最后一条用户提问」用相关子查询取。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings

ATTRIBUTIONS: tuple[str, ...] = ("语料缺失", "答案错误", "引用错误", "问题不清")
FEEDBACK_STATUSES: dict[str, str] = {
    "pending": "待归因",
    "attributed": "已归因",
    "resolved": "已补语料",
    "verified": "已复核",
}
# 动作 → (允许的起始状态, 目标状态)
FEEDBACK_ACTIONS: dict[str, tuple[str, str]] = {
    "attribute": ("pending", "attributed"),
    "resolve": ("attributed", "resolved"),
    "verify": ("resolved", "verified"),
}
UNMATCHED_STATUSES: dict[str, str] = {
    "pending": "待处理",
    "ignored": "已忽略",
    "resolved": "已补语料",
}


class FeedbackRuleError(ValueError):
    """归因/流转不合规，HTTP 层映射 400。"""


# ---------------------------------------------------------------------------
# 纯函数：归一化、流转校验（可单测，不依赖数据库）
# ---------------------------------------------------------------------------
_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:（）()【】\[\]\"'“”‘’《》<>~`]+")


def normalize_query(text: str | None) -> str:
    """归一化问题：去空白与标点、转小写，用于聚合相同问题。"""
    cleaned = _PUNCT.sub("", str(text or "").lower())
    return cleaned[:200]


def query_hash(text: str | None) -> str:
    return hashlib.sha1(normalize_query(text).encode("utf-8")).hexdigest()


def check_feedback_transition(
    current: str, action: str, attribution: str = "", remark: str = ""
) -> str:
    """校验反馈流转，返回目标状态；不合规抛 FeedbackRuleError。"""
    spec = FEEDBACK_ACTIONS.get((action or "").strip())
    if spec is None:
        raise FeedbackRuleError(f"不支持的操作：{action or '(空)'}")
    allowed_from, target = spec
    if current != allowed_from:
        raise FeedbackRuleError(
            f"当前状态为 {FEEDBACK_STATUSES.get(current, current)}，"
            f"不能执行该操作（要求 {FEEDBACK_STATUSES.get(allowed_from, allowed_from)}）"
        )
    attribution = (attribution or "").strip()
    remark = (remark or "").strip()
    if action == "attribute" and attribution not in ATTRIBUTIONS:
        raise FeedbackRuleError(f"归因必须是 {'/'.join(ATTRIBUTIONS)} 之一")
    if action == "resolve" and len(remark) < 2:
        raise FeedbackRuleError("标记已补语料时必须写明补了什么（至少 2 个字）")
    return target


def check_unmatched_status(status: str) -> str:
    status = (status or "").strip()
    if status not in UNMATCHED_STATUSES:
        raise FeedbackRuleError(f"状态必须是 {'/'.join(UNMATCHED_STATUSES)} 之一")
    return status


# ---------------------------------------------------------------------------
# 反馈列表 / 流转
# ---------------------------------------------------------------------------
def _require_mysql(settings: Settings) -> None:
    if not settings.mysql_url:
        raise FeedbackRuleError("反馈工作台需要连接 MySQL（内存模式不保存反馈）")


def list_feedback(
    settings: Settings,
    *,
    rating: int = 0,
    status: str = "",
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    _require_mysql(settings)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    keyword = (keyword or "").strip()
    status = (status or "").strip()
    if status and status not in FEEDBACK_STATUSES:
        raise FeedbackRuleError(f"状态必须是 {'/'.join(FEEDBACK_STATUSES)} 之一")

    from sqlalchemy import text

    from app.db.session import new_session

    where = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if rating in (1, -1):
        where.append("f.rating = :rating")
        params["rating"] = rating
    if status:
        where.append("f.handle_status = :status")
        params["status"] = status

    base = f"""
        SELECT f.id, f.message_id, f.user_id, u.username, f.rating, f.comment,
               f.attribution, f.handle_status, f.handler_id, f.handled_at,
               f.handle_remark, f.created_at,
               (SELECT mu.content FROM messages mu
                 WHERE mu.conversation_id = ma.conversation_id
                   AND mu.role = 'user' AND mu.id < ma.id
                 ORDER BY mu.id DESC LIMIT 1) AS question,
               LEFT(ma.content, 120) AS answer
          FROM feedback f
          JOIN messages ma ON ma.id = f.message_id
          LEFT JOIN users u ON u.id = f.user_id
         WHERE {' AND '.join(where)}
    """
    params_without_page = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    outer_where = "1=1"
    if keyword:
        outer_where = "(t.question LIKE :kw OR t.comment LIKE :kw)"
        params["kw"] = f"%{keyword}%"
        params_without_page["kw"] = params["kw"]

    sql = f"""
        SELECT * FROM ({base}) t
         WHERE {outer_where}
         ORDER BY t.created_at DESC, t.id DESC
         LIMIT :limit OFFSET :offset
    """
    count_sql = f"SELECT COUNT(*) FROM ({base}) t WHERE {outer_where}"

    with new_session(settings.mysql_url) as session:
        rows = session.execute(text(sql), params).mappings().all()
        total = int(session.execute(text(count_sql), params_without_page).scalar() or 0)

    return ([_feedback_item(dict(row)) for row in rows], total)


def _feedback_item(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["rating_label"] = "点赞" if int(item.get("rating") or 0) > 0 else "点踩"
    item["status_label"] = FEEDBACK_STATUSES.get(str(item.get("handle_status") or ""), "")
    for key in ("created_at", "handled_at"):
        item[key] = _fmt(item.get(key))
    return item


def _fmt(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def transition_feedback(
    settings: Settings,
    feedback_id: int,
    actor_id: int,
    *,
    action: str,
    attribution: str = "",
    remark: str = "",
) -> dict[str, Any] | None:
    _require_mysql(settings)

    from sqlalchemy import text

    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        row = session.execute(
            text(
                "SELECT id, handle_status, attribution FROM feedback WHERE id = :id"
            ),
            {"id": int(feedback_id)},
        ).mappings().first()
        if row is None:
            return None
        target = check_feedback_transition(
            str(row["handle_status"] or "pending"), action, attribution, remark
        )
        session.execute(
            text(
                "UPDATE feedback SET handle_status = :status, attribution = :attr, "
                "handler_id = :actor, handled_at = :now, handle_remark = :remark WHERE id = :id"
            ),
            {
                "status": target,
                "attr": (attribution or "").strip() or row["attribution"],
                "actor": actor_id,
                "now": datetime.now(),
                "remark": (remark or "").strip()[:500] or None,
                "id": int(feedback_id),
            },
        )
        session.commit()

    return {
        "id": int(feedback_id),
        "handle_status": target,
        "status_label": FEEDBACK_STATUSES.get(target, target),
    }


# ---------------------------------------------------------------------------
# 未命中问题：聚合 + 标记
# ---------------------------------------------------------------------------
def _parse_moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(value).replace("T", " ").split(".")[0], fmt)
        except ValueError:
            continue
    return None


def group_unmatched(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把拒答问题按归一化文本聚合成条目（纯函数）。"""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        text = str(row.get("question") or "").strip()
        if not text:
            continue
        digest = query_hash(text)
        moment = _parse_moment(row.get("created_at"))
        item = grouped.get(digest)
        if item is None:
            grouped[digest] = {
                "query_hash": digest,
                "query_text": text[:500],
                "count": 1,
                "first_seen": moment,
                "last_seen": moment,
                "status": "pending",
                "remark": None,
            }
            continue
        item["count"] += 1
        if moment is not None:
            if item["first_seen"] is None or moment < item["first_seen"]:
                item["first_seen"] = moment
            if item["last_seen"] is None or moment > item["last_seen"]:
                item["last_seen"] = moment

    ordered = sorted(
        grouped.values(),
        key=lambda x: (-int(x["count"]), x["last_seen"] or datetime.min),
    )
    for item in ordered:
        item["first_seen"] = _fmt(item["first_seen"])
        item["last_seen"] = _fmt(item["last_seen"])
    return ordered


def list_unmatched(
    settings: Settings,
    *,
    days: int = 30,
    status: str = "",
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    _require_mysql(settings)
    days = max(1, min(int(days or 30), 365))
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    status = (status or "").strip()
    if status and status not in UNMATCHED_STATUSES:
        raise FeedbackRuleError(f"状态必须是 {'/'.join(UNMATCHED_STATUSES)} 之一")

    from sqlalchemy import text

    from app.db.session import new_session

    since = datetime.now() - timedelta(days=days)
    sql = text(
        """
        SELECT mu.content AS question, ma.created_at
          FROM messages ma
          JOIN messages mu ON mu.id = (
                SELECT MAX(m2.id) FROM messages m2
                 WHERE m2.conversation_id = ma.conversation_id
                   AND m2.role = 'user' AND m2.id < ma.id
              )
         WHERE ma.role = 'assistant' AND ma.refused = 1 AND ma.created_at >= :since
        """
    )

    with new_session(settings.mysql_url) as session:
        rows = [dict(r) for r in session.execute(sql, {"since": since}).mappings().all()]
        items = group_unmatched(rows)
        marks = _load_marks(session, [i["query_hash"] for i in items])

    for item in items:
        mark = marks.get(item["query_hash"])
        if mark:
            item["status"] = mark["status"]
            item["remark"] = mark["remark"]

    if status:
        items = [i for i in items if i["status"] == status]
    if keyword:
        needle = keyword.lower()
        items = [i for i in items if needle in str(i["query_text"]).lower()]

    return items[offset : offset + limit], len(items)


def _load_marks(session: Any, hashes: list[str]) -> dict[str, dict[str, Any]]:
    from sqlalchemy import text

    if not hashes:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for chunk_start in range(0, len(hashes), 500):
        chunk = hashes[chunk_start : chunk_start + 500]
        placeholders = ", ".join(f":h{i}" for i in range(len(chunk)))
        params = {f"h{i}": value for i, value in enumerate(chunk)}
        rows = session.execute(
            text(
                f"SELECT query_hash, status, remark FROM unmatched_marks "
                f"WHERE query_hash IN ({placeholders})"
            ),
            params,
        ).mappings().all()
        for row in rows:
            result[str(row["query_hash"])] = dict(row)
    return result


def mark_unmatched(
    settings: Settings,
    *,
    digest: str,
    query_text: str,
    status: str,
    remark: str = "",
    actor_id: int,
) -> dict[str, Any]:
    _require_mysql(settings)
    status = check_unmatched_status(status)
    digest = (digest or "").strip().lower()
    if len(digest) != 40:
        raise FeedbackRuleError("query_hash 非法")

    from sqlalchemy import text

    from app.db.session import new_session

    now = datetime.now()
    with new_session(settings.mysql_url) as session:
        session.execute(
            text(
                "INSERT INTO unmatched_marks "
                "(query_hash, query_text, status, remark, handler_id, created_at, updated_at) "
                "VALUES (:hash, :text, :status, :remark, :actor, :now, :now) "
                "ON DUPLICATE KEY UPDATE status = VALUES(status), remark = VALUES(remark), "
                "handler_id = VALUES(handler_id), updated_at = VALUES(updated_at)"
            ),
            {
                "hash": digest,
                "text": (query_text or "")[:500] or digest,
                "status": status,
                "remark": (remark or "").strip()[:500] or None,
                "actor": actor_id,
                "now": now,
            },
        )
        session.commit()

    return {
        "query_hash": digest,
        "status": status,
        "status_label": UNMATCHED_STATUSES.get(status, status),
        "remark": (remark or "").strip() or None,
    }
