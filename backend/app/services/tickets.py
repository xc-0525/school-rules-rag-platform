"""工单创建服务（P1-7）。

两个入口共用同一套幂等规则：
- Tool Calling 的 `create_ticket`（用户已明确表达投诉/报修意图时直接建单）；
- `POST /api/v1/tickets`（用户在界面上确认草稿后建单）。

幂等：同一用户 + 同标题 + 同描述，在 `TICKET_IDEMPOTENCY_SECONDS` 窗口内
复用同一张工单，避免"重复问一句就多一张单"。

MySQL 与 Mock 两种实现：
- 有 MYSQL_URL：查 tickets 表（created_at 窗口内）；
- 无 MYSQL_URL：进程内 dict 缓存（重启失效，够本地演示用）。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.config import Settings


@dataclass
class TicketReceipt:
    ticket_no: str
    status: str = "open"
    reused: bool = False


_mock_lock = threading.Lock()
_mock_tickets: dict[tuple[int, str, str], tuple[float, str]] = {}


def _mock_ticket_no() -> str:
    return f"T{datetime.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}"


def _window(settings: Settings) -> int:
    return max(0, int(getattr(settings, "ticket_idempotency_seconds", 600) or 0))


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
    key = (user_id, title, description)
    window = _window(settings)
    now = time.time()
    with _mock_lock:
        hit = _mock_tickets.get(key)
        if hit and window > 0 and hit[0] >= now - window:
            return TicketReceipt(ticket_no=hit[1], reused=True)
        ticket_no = _mock_ticket_no()
        _mock_tickets[key] = (now, ticket_no)
        # 顺手清理过期键，避免长期运行内存增长
        if len(_mock_tickets) > 500:
            expired = [k for k, v in _mock_tickets.items() if v[0] < now - window]
            for expired_key in expired:
                _mock_tickets.pop(expired_key, None)
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


def clear_mock_tickets() -> None:
    """测试用：清空进程内工单缓存。"""
    with _mock_lock:
        _mock_tickets.clear()
