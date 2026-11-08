"""运营看板聚合（N-24）。

数据全部来自现有表，不新增埋点：
- 规模：messages（提问）、conversations（会话/活跃用户）
- 质量：messages.refused、feedback.rating
- 效率：messages.latency_ms、tool_call_logs.success
- 内容：未命中 Top（复用 feedback_admin 的聚合口径）

MySQL 5.5 兼容：无窗口函数，P50/P95 用 `ORDER BY ... LIMIT 1 OFFSET n`，
趋势用 GROUP BY DATE()，缺失日期在应用层补齐。

进程内 60 秒缓存：答辩现场反复刷新不会把库拖慢。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings

CACHE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_cache: dict[tuple[int, ...], tuple[float, dict[str, Any]]] = {}


class AnalyticsError(ValueError):
    """看板依赖 MySQL，内存模式无历史数据。"""


# ---------------------------------------------------------------------------
# 纯函数（可单测）
# ---------------------------------------------------------------------------
def rate(numerator: int | float, denominator: int | float) -> float:
    """比率，保留 4 位小数；分母为 0 返回 0。"""
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def percentile(values: list[int], fraction: float) -> int:
    """最近秩法取分位值（values 无需有序）。"""
    if not values:
        return 0
    ordered = sorted(int(v or 0) for v in values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def fill_trend(rows: list[dict[str, Any]], days: int, today: datetime | None = None) -> list[dict[str, Any]]:
    """把 GROUP BY 结果补齐为连续 days 天，缺失日期填 0。"""
    today = (today or datetime.now()).date()
    bucket = {str(row.get("day")): row for row in rows}
    result: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        key = day.isoformat()
        row = bucket.get(key) or {}
        result.append(
            {
                "date": key,
                "questions": int(row.get("questions") or 0),
                "refused": int(row.get("refused") or 0),
            }
        )
    return result


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def _require_mysql(settings: Settings) -> None:
    if not settings.mysql_url:
        raise AnalyticsError("运营看板需要连接 MySQL（内存模式没有历史数据）")


def overview(settings: Settings, days: int = 7) -> dict[str, Any]:
    _require_mysql(settings)
    days = max(1, min(int(days or 7), 90))
    cache_key = (days,)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and hit[0] > now:
            return hit[1]

    from sqlalchemy import text

    from app.db.session import new_session

    since = datetime.now() - timedelta(days=days)
    prev_since = since - timedelta(days=days)

    with new_session(settings.mysql_url) as session:
        questions = _scalar(
            session,
            "SELECT COUNT(*) FROM messages WHERE role = 'user' AND created_at >= :since",
            {"since": since},
        )
        prev_questions = _scalar(
            session,
            "SELECT COUNT(*) FROM messages WHERE role = 'user' "
            "AND created_at >= :prev AND created_at < :since",
            {"prev": prev_since, "since": since},
        )
        active_users = _scalar(
            session,
            "SELECT COUNT(DISTINCT c.user_id) FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.role = 'user' AND m.created_at >= :since",
            {"since": since},
        )
        conversations = _scalar(
            session,
            "SELECT COUNT(DISTINCT conversation_id) FROM messages "
            "WHERE role = 'user' AND created_at >= :since",
            {"since": since},
        )
        answers = _scalar(
            session,
            "SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND created_at >= :since",
            {"since": since},
        )
        refused = _scalar(
            session,
            "SELECT COUNT(*) FROM messages "
            "WHERE role = 'assistant' AND refused = 1 AND created_at >= :since",
            {"since": since},
        )
        feedback_total = _scalar(
            session,
            "SELECT COUNT(*) FROM feedback WHERE created_at >= :since",
            {"since": since},
        )
        likes = _scalar(
            session,
            "SELECT COUNT(*) FROM feedback WHERE rating = 1 AND created_at >= :since",
            {"since": since},
        )
        dislikes = _scalar(
            session,
            "SELECT COUNT(*) FROM feedback WHERE rating = -1 AND created_at >= :since",
            {"since": since},
        )
        tool_calls = _scalar(
            session,
            "SELECT COUNT(*) FROM tool_call_logs WHERE created_at >= :since",
            {"since": since},
        )
        tool_ok = _scalar(
            session,
            "SELECT COALESCE(SUM(success), 0) FROM tool_call_logs WHERE created_at >= :since",
            {"since": since},
        )
        latencies = [
            int(row[0] or 0)
            for row in session.execute(
                text(
                    "SELECT latency_ms FROM messages WHERE role = 'assistant' "
                    "AND created_at >= :since ORDER BY latency_ms"
                ),
                {"since": since},
            ).all()
        ]
        trend_rows = [
            dict(row)
            for row in session.execute(
                text(
                    "SELECT DATE(created_at) AS day, "
                    "SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END) AS questions, "
                    "SUM(CASE WHEN role = 'assistant' AND refused = 1 THEN 1 ELSE 0 END) AS refused "
                    "FROM messages WHERE created_at >= :since GROUP BY DATE(created_at)"
                ),
                {"since": since},
            ).mappings().all()
        ]
        low_rated = [
            dict(row)
            for row in session.execute(
                text(
                    """
                    SELECT f.id, f.comment, f.created_at,
                           (SELECT mu.content FROM messages mu
                             WHERE mu.conversation_id = ma.conversation_id
                               AND mu.role = 'user' AND mu.id < ma.id
                             ORDER BY mu.id DESC LIMIT 1) AS question
                      FROM feedback f
                      JOIN messages ma ON ma.id = f.message_id
                     WHERE f.rating = -1 AND f.created_at >= :since
                     ORDER BY f.created_at DESC, f.id DESC
                     LIMIT 10
                    """
                ),
                {"since": since},
            ).mappings().all()
        ]

    top_unmatched: list[dict[str, Any]] = []
    try:
        from app.services import feedback_admin as fb

        unmatched, _ = fb.list_unmatched(settings, days=days, limit=10)
        top_unmatched = [
            {"query_text": item["query_text"], "count": item["count"], "last_seen": item["last_seen"]}
            for item in unmatched[:10]
        ]
    except Exception:  # noqa: BLE001 —— 未命中聚合失败不应让整块看板 500
        top_unmatched = []

    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "scale": {
            "questions": questions,
            "prev_questions": prev_questions,
            "delta_rate": rate(questions - prev_questions, prev_questions) if prev_questions else None,
            "active_users": active_users,
            "conversations": conversations,
            "avg_questions_per_conversation": rate(questions, conversations),
        },
        "quality": {
            "answers": answers,
            "refused": refused,
            "refusal_rate": rate(refused, answers),
            "feedback_total": feedback_total,
            "likes": likes,
            "dislikes": dislikes,
            "like_rate": rate(likes, feedback_total),
        },
        "efficiency": {
            "p50_ms": percentile(latencies, 0.50),
            "p95_ms": percentile(latencies, 0.95),
            "tool_calls": tool_calls,
            "tool_success_rate": rate(tool_ok, tool_calls),
        },
        "trend": fill_trend(trend_rows, days),
        "top_unmatched": top_unmatched,
        "low_rated": [
            {
                "id": int(row["id"]),
                "question": row["question"],
                "comment": row["comment"],
                "created_at": _fmt(row["created_at"]),
            }
            for row in low_rated
        ],
    }

    with _cache_lock:
        _cache[cache_key] = (now + CACHE_TTL_SECONDS, data)
    return data


def _scalar(session: Any, sql: str, params: dict[str, Any]) -> int:
    from sqlalchemy import text

    return int(session.execute(text(sql), params).scalar() or 0)


def _fmt(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value else None


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
