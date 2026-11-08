"""工单服务（P1-7 创建 + N-01 闭环）。

创建入口两个，共用同一套幂等规则：
- Tool Calling 的 `create_ticket`：只出草稿，不落库（M-01）；
- `POST /api/v1/tickets`：用户在界面上确认草稿后落库。

闭环（N-01）：
- 学生：只看自己的工单；
- 管理员：看全部，可受理 / 记录处理进展 / 关闭；
- 状态机 open → processing → closed，closed 为终态；
- 关闭必须填处理意见（≥5 字），学生能在“我的工单”看到。

双实现：有 MYSQL_URL 走 tickets 表；否则进程内 dict（本地演示 + 单测）。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings

TICKET_STATUSES: dict[str, str] = {
    "open": "待受理",
    "processing": "处理中",
    "closed": "已关闭",
}
ACTIVE_TICKET_STATUSES = ("open", "processing")

# 动作 → 允许的起始状态 / 目标状态；close 覆盖 open 与 processing
TICKET_ACTIONS: dict[str, dict[str, Any]] = {
    "accept": {"from": {"open"}, "to": "processing"},
    "process": {"from": {"processing"}, "to": "processing"},
    "close": {"from": {"open", "processing"}, "to": "closed"},
}
CLOSE_REMARK_MIN = 5


class TicketRuleError(ValueError):
    """业务规则不满足（非法流转 / 备注不合规），HTTP 层映射为 400。"""


class TicketPermissionError(TicketRuleError):
    """非管理员调用管理动作，HTTP 层映射为 403。"""


@dataclass
class TicketReceipt:
    ticket_no: str
    status: str = "open"
    reused: bool = False


_mock_lock = threading.Lock()
_mock_seq = 0
# id -> 完整工单记录；另用索引表维护幂等键，避免每次全表扫描
_mock_tickets: dict[int, dict[str, Any]] = {}
_mock_index: dict[tuple[int, str, str], int] = {}


def _mock_ticket_no() -> str:
    return f"T{datetime.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}"


def _window(settings: Settings) -> int:
    return max(0, int(getattr(settings, "ticket_idempotency_seconds", 600) or 0))


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


def status_label(status: str | None) -> str:
    return TICKET_STATUSES.get(str(status or ""), str(status or ""))


def _decorate(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["status_label"] = status_label(item.get("status"))
    for key in ("created_at", "updated_at", "closed_at"):
        if key in item:
            item[key] = _fmt(item[key])
    return item


def clear_mock_tickets() -> None:
    """测试用：清空进程内工单缓存。"""
    global _mock_seq
    with _mock_lock:
        _mock_tickets.clear()
        _mock_index.clear()
        _mock_seq = 0


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------
def create_ticket(
    settings: Settings,
    user_id: int,
    title: str,
    description: str,
    category: str | None = None,
) -> TicketReceipt:
    """创建工单（幂等）。调用方负责参数非空校验。"""
    title = (title or "").strip()[:200]
    description = (description or "").strip()
    category = (category or "").strip()[:50] or "其他"
    owner = int(user_id or 1)

    if settings.mysql_url:
        return _create_in_db(settings, owner, title, description, category)
    return _create_in_memory(settings, owner, title, description, category)


def _create_in_memory(
    settings: Settings, user_id: int, title: str, description: str, category: str
) -> TicketReceipt:
    global _mock_seq
    key = (user_id, title, description)
    window = _window(settings)
    now = datetime.now()
    with _mock_lock:
        hit_id = _mock_index.get(key)
        if hit_id is not None and window > 0:
            row = _mock_tickets.get(hit_id)
            created = _parse_dt(row.get("created_at")) if row else None
            if row is not None and created is not None and (now - created).total_seconds() <= window:
                return TicketReceipt(ticket_no=str(row["ticket_no"]), status=str(row["status"]), reused=True)

        _mock_seq += 1
        ticket_no = _mock_ticket_no()
        _mock_tickets[_mock_seq] = {
            "id": _mock_seq,
            "ticket_no": ticket_no,
            "user_id": user_id,
            "username": None,
            "title": title,
            "description": description,
            "category": category,
            "status": "open",
            "handler_id": None,
            "handler_remark": None,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
        }
        _mock_index[key] = _mock_seq
        # 顺手清理过期索引，避免长期运行内存增长（保留最近窗口内的记录）
        if len(_mock_index) > 500:
            expired = [
                k
                for k, ticket_id in _mock_index.items()
                if (created := _parse_dt(_mock_tickets.get(ticket_id, {}).get("created_at"))) is None
                or (now - created).total_seconds() > window
            ]
            for expired_key in expired:
                _mock_index.pop(expired_key, None)
        return TicketReceipt(ticket_no=ticket_no)


def _create_in_db(
    settings: Settings, user_id: int, title: str, description: str, category: str
) -> TicketReceipt:
    from app.db.models import Ticket
    from app.db.session import new_session

    window = _window(settings)
    with new_session(settings.mysql_url) as session:
        if window > 0:
            existing = (
                session.query(Ticket)
                .filter(
                    Ticket.user_id == user_id,
                    Ticket.title == title,
                    Ticket.description == description,
                    Ticket.created_at >= datetime.now() - timedelta(seconds=window),
                )
                .order_by(Ticket.id.desc())
                .first()
            )
            if existing is not None:
                return TicketReceipt(
                    ticket_no=existing.ticket_no,
                    status=str(existing.status or "open"),
                    reused=True,
                )

        ticket = Ticket(
            ticket_no=_mock_ticket_no(),  # 先用临时号满足唯一约束，flush 后换成含 id 的正式号
            user_id=user_id,
            title=title,
            description=description,
            category=category,
            status="open",
            updated_at=datetime.now(),
        )
        session.add(ticket)
        session.flush()
        ticket.ticket_no = f"T{datetime.now():%Y%m%d}{int(ticket.id):06d}"
        session.commit()
        return TicketReceipt(ticket_no=ticket.ticket_no)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def list_tickets(
    settings: Settings,
    *,
    user_id: int,
    is_admin: bool,
    status: str = "",
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """返回 (items, total)。学生只看本人，管理员看全部。"""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    status = (status or "").strip()
    keyword = (keyword or "").strip()

    if status and status not in TICKET_STATUSES:
        raise TicketRuleError(f"状态必须是 {', '.join(TICKET_STATUSES)} 之一")

    if not settings.mysql_url:
        with _mock_lock:
            rows = [
                dict(r)
                for r in _mock_tickets.values()
                if (is_admin or r["user_id"] == user_id)
                and (not status or r["status"] == status)
                and (not keyword or _match_keyword(r, keyword))
            ]
        rows.sort(key=lambda r: (_parse_dt(r["created_at"]) or datetime.min, r["id"]), reverse=True)
        return [_decorate(r) for r in rows[offset : offset + limit]], len(rows)

    from sqlalchemy import or_

    from app.db.models import Ticket
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        query = session.query(Ticket)
        if not is_admin:
            query = query.filter(Ticket.user_id == user_id)
        if status:
            query = query.filter(Ticket.status == status)
        if keyword:
            like = f"%{keyword}%"
            # 过滤下推到 SQL，不在应用层捞全表
            query = query.filter(
                or_(
                    Ticket.ticket_no.like(like),
                    Ticket.title.like(like),
                    Ticket.description.like(like),
                )
            )
        total = query.count()
        rows = (
            query.order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = _usernames(session, {int(r.user_id) for r in rows})
        return ([_row_dict(r, names.get(int(r.user_id))) for r in rows], int(total))


def _match_keyword(row: dict[str, Any], keyword: str) -> bool:
    needle = keyword.lower()
    haystack = f"{row.get('ticket_no', '')} {row.get('title', '')} {row.get('description', '')}".lower()
    return needle in haystack


def _usernames(session: Any, user_ids: set[int]) -> dict[int, str]:
    from app.db.models import User

    if not user_ids:
        return {}
    rows = session.query(User.id, User.username).filter(User.id.in_(user_ids)).all()
    return {int(uid): str(name) for uid, name in rows}


def _row_dict(row: Any, username: str | None = None) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "ticket_no": row.ticket_no,
        "user_id": int(row.user_id),
        "username": username,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "status": row.status,
        "handler_id": row.handler_id,
        "handler_remark": row.handler_remark,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "closed_at": getattr(row, "closed_at", None),
    }


def get_ticket(
    settings: Settings,
    ticket_id: int,
    user_id: int,
    is_admin: bool,
) -> dict[str, Any] | None:
    """详情：学生只能看自己的；看不到别人的返回 None（不暴露存在性）。"""
    if not settings.mysql_url:
        with _mock_lock:
            row = _mock_tickets.get(int(ticket_id))
            row = dict(row) if row else None
        if row is None:
            return None
        if not is_admin and row["user_id"] != user_id:
            return None
        return _decorate(row)

    from app.db.models import Ticket
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(Ticket, int(ticket_id))
        if r is None:
            return None
        if not is_admin and int(r.user_id) != user_id:
            return None
        names = _usernames(session, {int(r.user_id)})
        return _decorate(_row_dict(r, names.get(int(r.user_id))))


# ---------------------------------------------------------------------------
# 流转
# ---------------------------------------------------------------------------
def transition_ticket(
    settings: Settings,
    ticket_id: int,
    actor_id: int,
    *,
    is_admin: bool,
    action: str,
    remark: str = "",
) -> dict[str, Any] | None:
    """受理 / 记录进展 / 关闭。

    - 仅管理员可流转；非管理员抛 TicketPermissionError（HTTP 403）；
    - 目标不存在或无权查看返回 None（HTTP 404）；
    - 非法流转、备注不合规抛 TicketRuleError（HTTP 400）。
    """
    action = (action or "").strip().lower()
    spec = TICKET_ACTIONS.get(action)
    if spec is None:
        raise TicketRuleError(f"不支持的操作：{action or '(空)'}")
    if not is_admin:
        raise TicketPermissionError("仅管理员可流转工单")

    remark = (remark or "").strip()
    if action == "close":
        if len(remark) < CLOSE_REMARK_MIN:
            raise TicketRuleError(f"关闭工单必须填写至少 {CLOSE_REMARK_MIN} 个字的处理意见")
    elif action == "process" and not remark:
        raise TicketRuleError("记录处理进展时必须填写处理意见")

    now = datetime.now()
    if not settings.mysql_url:
        with _mock_lock:
            row = _mock_tickets.get(int(ticket_id))
            if row is None:
                return None
            _check_transition(row["status"], spec["from"], action)
            row["status"] = spec["to"]
            row["handler_id"] = actor_id
            if remark:
                row["handler_remark"] = remark[:4000]
            row["updated_at"] = now
            if spec["to"] == "closed":
                row["closed_at"] = now
            return _decorate(dict(row))

    from app.db.models import Ticket
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(Ticket, int(ticket_id))
        if r is None:
            return None
        _check_transition(str(r.status), spec["from"], action)
        r.status = spec["to"]
        r.handler_id = actor_id
        if remark:
            r.handler_remark = remark[:4000]
        r.updated_at = now
        if spec["to"] == "closed":
            r.closed_at = now
        session.commit()
        names = _usernames(session, {int(r.user_id)})
        return _decorate(_row_dict(r, names.get(int(r.user_id))))


def _check_transition(current: str, allowed_from: set[str], action: str) -> None:
    if current not in allowed_from:
        raise TicketRuleError(
            f"当前状态为 {status_label(current)}，不能执行该操作"
            f"（允许的状态：{'/'.join(status_label(s) for s in sorted(allowed_from))}）"
        )
