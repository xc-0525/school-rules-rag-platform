"""SQLAlchemy ORM 模型（与 sql/init_mysql.sql 对应）。

注意：生产/演示环境用 sql/init_mysql.sql 建表，**不要**用 create_all，
因为本机 MySQL 5.5 与 8.0 的 DDL 细节不同，脚本已经处理了兼容性。
模型只负责查询和写入。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    TIMESTAMP,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password: Mapped[str] = mapped_column(String(200))
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True)
    role: Mapped[str] = mapped_column(Enum("student", "admin", name="user_role"), default="student")
    status: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(100), default="新会话")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(Enum("user", "assistant", "system", name="message_role"))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    refused: Mapped[int] = mapped_column(Integer, default=0)
    degraded: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("messages.id"))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str] = mapped_column(String(20), default="txt")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        Enum("pending", "processing", "done", "failed", name="document_status"), default="pending"
    )
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"))
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    chapter: Mapped[str | None] = mapped_column(String(200), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    is_pinned: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # 公告模块新增：分类与有效期（见 sql/migrations/002_notice_fields.sql）
    category: Mapped[str] = mapped_column(String(32), default="other")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # normal=普通；urgent=紧急。urgent 且置顶时在学生端顶部横幅展示
    # （见 sql/migrations/003_notice_priority.sql）
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DocumentTag(Base):
    __tablename__ = "document_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())


class DocumentTagRelation(Base):
    __tablename__ = "document_tag_relations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"))
    tag_id: Mapped[int] = mapped_column(Integer, ForeignKey("document_tags.id"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())


class Ticket(Base):
    """转人工 / 投诉工单（create_ticket Tool 真实写入）。"""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ToolCallLog(Base):
    """Tool Calling 审计日志（课程要求的 Agent 可观测性）。"""

    __tablename__ = "tool_call_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(50))
    arguments: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())


# ---------------------------------------------------------------------------
# 校园服务模块（报修 / 图书馆预约 / 失物招领）
# 设计与状态流转见 docs/campus-modules.md；迁移见 sql/migrations/004_*.sql
# ---------------------------------------------------------------------------


class RepairRequest(Base):
    """寝室报修申请。独立于 tickets：状态机、限流规则、字段均不同，
    复用会让 tickets 承担两个职责（违反模块解耦）。"""

    __tablename__ = "repair_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), default="other")
    building: Mapped[str] = mapped_column(String(100))
    room: Mapped[str] = mapped_column(String(50))
    contact: Mapped[str] = mapped_column(String(100), default="")
    # pending → processing → completed；pending/processing 可 closed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    handler_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handler_remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReservableLocation(Base):
    """可预约的图书馆位置（座位区/研讨间）。"""

    __tablename__ = "reservable_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    zone: Mapped[str] = mapped_column(String(50), default="")
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())


class LocationReservation(Base):
    """位置预约记录。UNIQUE(location_id, res_date, slot) 由数据库兜底防超卖。"""

    __tablename__ = "location_reservations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    location_id: Mapped[int] = mapped_column(Integer, ForeignKey("reservable_locations.id"))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    res_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD，跨库一致用字符串
    slot: Mapped[str] = mapped_column(String(20))      # morning / afternoon / evening
    # reserved → fulfilled / cancelled / no_show
    status: Mapped[str] = mapped_column(String(20), default="reserved")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LostFoundItem(Base):
    """失物招领条目：先审核后发布。"""

    __tablename__ = "lost_found_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(10))  # lost = 我丢了东西 / found = 我捡到东西
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(String(200), default="")
    contact: Mapped[str] = mapped_column(String(100), default="")
    image_urls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 数组
    # pending_review → published / rejected → resolved / removed
    status: Mapped[str] = mapped_column(String(20), default="pending_review")
    reviewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
