"""持久化适配器：问答记录 / 会话历史 / 反馈。

同一个接口两套实现：
- MySQLPersistence：真实 MySQL（SQLAlchemy + pymysql），Day2 起默认使用；
- MockPersistence：进程内内存实现，无数据库时兜底（测试 / 现场应急）。
异步接口内部把同步数据库调用丢到线程池，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from typing import Any, Protocol

from app.agents.state import AgentState

# 演示阶段默认用户（admin，sql/init_mysql.sql 里种好的 id=1）；接入登录后从 token 取
DEMO_USER_ID = 1


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


class Persistence(Protocol):
    async def save_qa(self, state: AgentState) -> int | None: ...

    async def list_conversations(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]: ...

    async def get_conversation(self, conversation_id: int, user_id: int) -> dict[str, Any] | None: ...

    async def recent_messages(
        self, conversation_id: int, user_id: int, limit: int = 6
    ) -> list[dict[str, str]]: ...

    async def add_feedback(
        self, message_id: int, user_id: int, rating: int, comment: str | None
    ) -> dict[str, Any] | None: ...


# ---------------------------------------------------------------------------
# Mock：内存实现
# ---------------------------------------------------------------------------
class MockPersistence:
    """内存版持久化：不依赖 MySQL，用于单元测试和现场兜底。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conversation_seq = 0
        self._message_seq = 0
        self._feedback_seq = 0
        self._conversations: dict[int, dict[str, Any]] = {}
        self._messages: dict[int, dict[str, Any]] = {}
        self._feedback: dict[tuple[int, int], dict[str, Any]] = {}

    async def save_qa(self, state: AgentState) -> int | None:
        user_id = int(getattr(state, "user_id", DEMO_USER_ID) or DEMO_USER_ID)
        with self._lock:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conversation_id = state.conversation_id
            conversation = (
                self._conversations.get(conversation_id)
                if conversation_id
                else None
            )
            if conversation is None or conversation["user_id"] != user_id:
                self._conversation_seq += 1
                conversation_id = self._conversation_seq
                self._conversations[conversation_id] = {
                    "id": conversation_id,
                    "user_id": user_id,
                    "title": (state.query or "新会话")[:30],
                    "created_at": now,
                    "updated_at": now,
                    "message_count": 0,
                }
            conversation = self._conversations[conversation_id]

            self._append_message(conversation_id, "user", state.query, [], 0, False, False)
            self._append_message(
                conversation_id,
                "assistant",
                state.answer,
                [source.model_dump() for source in state.sources],
                state.latency_ms,
                state.refused,
                state.degraded,
            )
            conversation["updated_at"] = now
            conversation["message_count"] += 2
            return conversation_id

    def _append_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        sources: list[dict[str, Any]],
        latency_ms: int,
        refused: bool,
        degraded: bool,
    ) -> None:
        self._message_seq += 1
        self._messages[self._message_seq] = {
            "id": self._message_seq,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "sources": sources,
            "latency_ms": latency_ms,
            "refused": refused,
            "degraded": degraded,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def list_conversations(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = [row for row in self._conversations.values() if row["user_id"] == user_id]
            rows.sort(key=lambda row: row["updated_at"] or "", reverse=True)
            return [dict(row) for row in rows[:limit]]

    async def get_conversation(self, conversation_id: int, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation["user_id"] != user_id:
                return None
            messages = [
                dict(message)
                for message in self._messages.values()
                if message["conversation_id"] == conversation_id
            ]
            messages.sort(key=lambda message: message["id"])
            return {**conversation, "messages": messages}

    async def add_feedback(
        self, message_id: int, user_id: int, rating: int, comment: str | None
    ) -> dict[str, Any] | None:
        with self._lock:
            if message_id not in self._messages:
                return None
            key = (message_id, user_id)
            if key not in self._feedback:
                self._feedback_seq += 1
                self._feedback[key] = {
                    "id": self._feedback_seq,
                    "message_id": message_id,
                    "rating": rating,
                    "comment": comment,
                }
            else:
                self._feedback[key].update({"rating": rating, "comment": comment})
            return dict(self._feedback[key])

    async def recent_messages(
        self, conversation_id: int, user_id: int, limit: int = 6
    ) -> list[dict[str, str]]:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation["user_id"] != user_id:
                return []
            messages = [
                message
                for message in self._messages.values()
                if message["conversation_id"] == conversation_id
            ]
            messages.sort(key=lambda message: message["id"])
            return [
                {"role": str(message["role"]), "content": str(message["content"])}
                for message in messages[-max(1, limit):]
            ]


# ---------------------------------------------------------------------------
# MySQL：真实实现
# ---------------------------------------------------------------------------
class MySQLPersistence:
    """真实 MySQL 持久化（SQLAlchemy 同步引擎 + 线程池调用）。"""

    def __init__(self, url: str) -> None:
        self.url = url

    # ---- 问答落库 ----
    async def save_qa(self, state: AgentState) -> int | None:
        return await asyncio.to_thread(self._save_qa_sync, state)

    def _save_qa_sync(self, state: AgentState) -> int:
        from app.db.models import Conversation, Message
        from app.db.session import new_session

        user_id = int(getattr(state, "user_id", DEMO_USER_ID) or DEMO_USER_ID)
        with new_session(self.url) as session:
            conversation = None
            if state.conversation_id:
                conversation = session.get(Conversation, int(state.conversation_id))
                if conversation is not None and conversation.user_id != user_id:
                    conversation = None  # 不属于当前用户 → 新建会话
            if conversation is None:
                conversation = Conversation(
                    user_id=user_id,
                    title=(state.query or "新会话")[:30],
                    updated_at=datetime.now(),
                )
                session.add(conversation)
                session.flush()

            sources_json = (
                json.dumps([source.model_dump() for source in state.sources], ensure_ascii=False)
                if state.sources
                else None
            )
            session.add_all(
                [
                    Message(
                        conversation_id=conversation.id,
                        role="user",
                        content=state.query,
                        tokens=0,
                        latency_ms=0,
                        refused=0,
                        degraded=0,
                    ),
                    Message(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=state.answer,
                        sources=sources_json,
                        tokens=0,
                        latency_ms=int(state.latency_ms or 0),
                        refused=1 if state.refused else 0,
                        degraded=1 if state.degraded else 0,
                    ),
                ]
            )
            conversation.updated_at = datetime.now()
            session.commit()
            return int(conversation.id)

    # ---- 会话列表 / 详情 ----
    async def list_conversations(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_conversations_sync, user_id, limit)

    def _list_conversations_sync(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        from sqlalchemy import func, select

        from app.db.models import Conversation, Message
        from app.db.session import new_session

        with new_session(self.url) as session:
            rows = session.execute(
                select(Conversation, func.count(Message.id).label("message_count"))
                .outerjoin(Message, Message.conversation_id == Conversation.id)
                .where(Conversation.user_id == user_id)
                .group_by(Conversation.id)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "id": conversation.id,
                    "user_id": conversation.user_id,
                    "title": conversation.title,
                    "created_at": _iso(conversation.created_at),
                    "updated_at": _iso(conversation.updated_at),
                    "message_count": int(count or 0),
                }
                for conversation, count in rows
            ]

    async def get_conversation(self, conversation_id: int, user_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_conversation_sync, conversation_id, user_id)

    def _get_conversation_sync(self, conversation_id: int, user_id: int) -> dict[str, Any] | None:
        from sqlalchemy import select

        from app.db.models import Conversation, Message
        from app.db.session import new_session

        with new_session(self.url) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                return None
            messages = (
                session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id)
                )
                .scalars()
                .all()
            )
            message_rows = []
            for message in messages:
                sources: list[dict[str, Any]] = []
                if message.sources:
                    try:
                        sources = json.loads(message.sources)
                    except (TypeError, ValueError):
                        sources = []
                message_rows.append(
                    {
                        "id": message.id,
                        "conversation_id": message.conversation_id,
                        "role": message.role,
                        "content": message.content,
                        "sources": sources,
                        "refused": bool(message.refused),
                        "degraded": bool(message.degraded),
                        "latency_ms": message.latency_ms,
                        "created_at": _iso(message.created_at),
                    }
                )
            return {
                "id": conversation.id,
                "user_id": conversation.user_id,
                "title": conversation.title,
                "created_at": _iso(conversation.created_at),
                "updated_at": _iso(conversation.updated_at),
                "message_count": len(message_rows),
                "messages": message_rows,
            }

    # ---- 反馈 ----
    async def add_feedback(
        self, message_id: int, user_id: int, rating: int, comment: str | None
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._add_feedback_sync, message_id, user_id, rating, comment)

    async def recent_messages(
        self, conversation_id: int, user_id: int, limit: int = 6
    ) -> list[dict[str, str]]:
        return await asyncio.to_thread(
            self._recent_messages_sync, conversation_id, user_id, limit
        )

    def _recent_messages_sync(
        self, conversation_id: int, user_id: int, limit: int
    ) -> list[dict[str, str]]:
        from sqlalchemy import select

        from app.db.models import Conversation, Message
        from app.db.session import new_session

        with new_session(self.url) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                return []
            rows = (
                session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id.desc())
                    .limit(max(1, limit))
                )
                .scalars()
                .all()
            )
        return [
            {"role": str(message.role), "content": message.content}
            for message in reversed(rows)
        ]

    def _add_feedback_sync(
        self, message_id: int, user_id: int, rating: int, comment: str | None
    ) -> dict[str, Any] | None:
        from sqlalchemy import select

        from app.db.models import Feedback, Message
        from app.db.session import new_session

        with new_session(self.url) as session:
            if session.get(Message, message_id) is None:
                return None
            feedback = session.execute(
                select(Feedback).where(
                    Feedback.message_id == message_id, Feedback.user_id == user_id
                )
            ).scalar_one_or_none()
            if feedback is None:
                feedback = Feedback(
                    message_id=message_id, user_id=user_id, rating=rating, comment=comment
                )
                session.add(feedback)
            else:
                feedback.rating = rating
                feedback.comment = comment
            session.commit()
            return {
                "id": feedback.id,
                "message_id": message_id,
                "rating": feedback.rating,
                "comment": feedback.comment,
            }
