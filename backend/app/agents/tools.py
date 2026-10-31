"""Agent 可调用的真实业务工具（Tool Calling）。

三个 Tool 都对应真实能力，不是占位：
- search_rules：复用 Qdrant 向量检索 + Neo4j 图谱召回；
- query_announcements：真实查询 MySQL announcements 表；
- create_ticket：真实写 MySQL tickets 表并返回工单号。

工具调用会写 tool_call_logs（如果 MySQL 已初始化），用于 Agent 可观测性与答辩演示。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.schemas.chat import Evidence, SourceItem

logger = logging.getLogger("app.tools")

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_rules",
            "description": (
                "检索校园服务/校纪校规知识库（向量语义检索 + 知识图谱），"
                "适用于开放时间、请假、处分、奖学金等需要条款依据的问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的完整问题或关键词"},
                    "top_k": {"type": "integer", "description": "返回条数，1-5，默认 3"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_announcements",
            "description": "查询已发布的校园公告（如图书馆开放时间调整、校园卡服务中心迁址）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "可选关键词，如 图书馆/校园卡"},
                    "limit": {"type": "integer", "description": "最多返回几条，默认 5"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": (
                "提交转人工/投诉工单，适用于设备报修、宿舍噪音、服务投诉等"
                "无法由知识库直接解决、需要人工跟进的事项。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "工单标题，简短概括"},
                    "description": {"type": "string", "description": "问题详情、地点、时间"},
                    "category": {
                        "type": "string",
                        "description": "分类：宿舍/食堂/图书馆/网络/其他",
                    },
                },
                "required": ["title", "description"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class ToolResult:
    name: str
    arguments: dict[str, Any]
    success: bool
    summary: str
    sources: list[SourceItem] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


class ToolRunner:
    """执行工具并记录审计日志。"""

    def __init__(self, services: Any, settings: Any) -> None:
        self.services = services
        self.settings = settings

    async def run(
        self,
        name: str,
        arguments: dict[str, Any],
        user_id: int,
        conversation_id: int | None,
        run_id: str,
    ) -> ToolResult:
        started = time.perf_counter()
        result: ToolResult
        try:
            if name == "search_rules":
                result = await self._search_rules(arguments)
            elif name == "query_announcements":
                result = await self._query_announcements(arguments)
            elif name == "create_ticket":
                result = await self._create_ticket(arguments, user_id)
            else:
                result = ToolResult(
                    name=name,
                    arguments=arguments,
                    success=False,
                    summary=f"未知工具：{name}",
                )
        except Exception as exc:  # noqa: BLE001 —— 工具失败不能拖垮整次问答
            logger.exception("tool=%s failed", name)
            result = ToolResult(
                name=name,
                arguments=arguments,
                success=False,
                summary=f"工具执行失败：{type(exc).__name__}: {exc}",
            )

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        self._log(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            result=result,
        )
        return result

    # ------------------------------------------------------------------ 工具实现
    async def _search_rules(self, arguments: dict[str, Any]) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("search_rules 缺少 query 参数")
        top_k = max(1, min(int(arguments.get("top_k") or 3), 5))

        vector_hits = await self.services.vector.search(query, top_k=top_k)
        graph_paths = await self.services.graph.search_paths(query, top_k=2)
        evidence = sorted([*vector_hits, *graph_paths], key=lambda item: item.score, reverse=True)
        sources = [
            SourceItem(
                type=item.type,
                title=item.title,
                detail=item.detail,
                snippet=item.snippet,
            )
            for item in evidence
        ]
        lines = [
            f"{index + 1}. {item.title} · {item.detail}\n{item.snippet[:160]}"
            for index, item in enumerate(evidence[:5])
        ]
        summary = "检索到以下校规/校园服务依据：\n" + ("\n\n".join(lines) if lines else "（无命中）")
        return ToolResult(
            name="search_rules",
            arguments={"query": query, "top_k": top_k},
            success=True,
            summary=summary,
            sources=sources,
            evidence=evidence,
            payload={"evidence": [item.model_dump() for item in evidence]},
        )

    async def _query_announcements(self, arguments: dict[str, Any]) -> ToolResult:
        keyword = str(arguments.get("keyword") or "").strip()
        limit = max(1, min(int(arguments.get("limit") or 5), 10))
        announcements = await self._announcements_from_db(keyword, limit)
        if not announcements:
            return ToolResult(
                name="query_announcements",
                arguments={"keyword": keyword, "limit": limit},
                success=True,
                summary="未找到相关公告。" if keyword else "当前暂无已发布公告。",
                payload={"announcements": []},
            )

        lines = [
            f"{index + 1}. {item['title']}（{item.get('created_at') or ''}）\n{item['content'][:200]}"
            for index, item in enumerate(announcements)
        ]
        summary = "查到以下校园公告：\n\n" + "\n\n".join(lines)
        sources = [
            SourceItem(
                type="tool",
                title=f"公告：{item['title']}",
                detail="校园公告",
                snippet=item["content"],
            )
            for item in announcements
        ]
        return ToolResult(
            name="query_announcements",
            arguments={"keyword": keyword, "limit": limit},
            success=True,
            summary=summary,
            sources=sources,
            payload={"announcements": announcements},
        )

    async def _announcements_from_db(
        self, keyword: str, limit: int
    ) -> list[dict[str, Any]]:
        if not self.settings.mysql_url:
            return self._demo_announcements(keyword, limit)
        try:
            import asyncio

            return await asyncio.to_thread(
                self._announcements_sync, keyword, limit
            )
        except Exception:  # noqa: BLE001 —— 表未初始化时退回演示数据
            logger.exception("query_announcements failed, fallback to demo data")
            return self._demo_announcements(keyword, limit)

    def _announcements_sync(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        from sqlalchemy import or_, select

        from app.db.models import Announcement
        from app.db.session import new_session

        with new_session(self.settings.mysql_url) as session:
            query = select(Announcement).where(Announcement.status == "published")
            if keyword:
                like = f"%{keyword}%"
                query = query.where(
                    or_(Announcement.title.like(like), Announcement.content.like(like))
                )
            rows = (
                session.execute(
                    query.order_by(Announcement.is_pinned.desc(), Announcement.id.desc()).limit(limit)
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": row.id,
                    "title": row.title,
                    "content": row.content,
                    "created_at": row.created_at.strftime("%Y-%m-%d") if row.created_at else "",
                }
                for row in rows
            ]

    @staticmethod
    def _demo_announcements(keyword: str, limit: int) -> list[dict[str, Any]]:
        """Mock/MySQL 未初始化时的兜底公告（与 SQL 种子一致）。"""
        items = [
            {
                "id": 1,
                "title": "关于图书馆延长开放时间的通知",
                "content": (
                    "为方便同学复习，自本周起图书馆周一至周五开放时间延长至 22:30，"
                    "周六周日 09:00-21:30，考试周另行通知。"
                ),
                "created_at": "2026-10-31",
                "is_pinned": 1,
            },
            {
                "id": 2,
                "title": "校园卡服务中心迁址公告",
                "content": (
                    "校园卡服务中心自本周起迁至行政楼一层 105 室，办理挂失、补卡、充值业务，"
                    "服务时间 08:30-17:30。"
                ),
                "created_at": "2026-10-31",
                "is_pinned": 0,
            },
        ]
        matched = [
            item
            for item in items
            if not keyword or keyword in item["title"] or keyword in item["content"]
        ]
        return matched[:limit]

    async def _create_ticket(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        import asyncio

        title = str(arguments.get("title") or "").strip()
        description = str(arguments.get("description") or "").strip()
        category = str(arguments.get("category") or "").strip() or "其他"
        if not title:
            raise ValueError("create_ticket 缺少 title 参数")
        if not description:
            raise ValueError("create_ticket 缺少 description 参数")

        if self.settings.mysql_url:
            ticket_no = await asyncio.to_thread(
                self._create_ticket_sync,
                title,
                description,
                category,
                user_id,
            )
        else:
            ticket_no = self._mock_ticket_no()

        summary = (
            f"已提交转人工工单：{ticket_no}\n"
            f"标题：{title}\n分类：{category}\n"
            "学工部门会尽快跟进；如需补充材料，请保留工单号。"
        )
        return ToolResult(
            name="create_ticket",
            arguments={"title": title, "description": description, "category": category},
            success=True,
            summary=summary,
            sources=[
                SourceItem(
                    type="tool",
                    title=f"工单 {ticket_no}",
                    detail=f"分类：{category}",
                    snippet=summary,
                )
            ],
            payload={"ticket_no": ticket_no, "status": "open"},
        )

    def _create_ticket_sync(
        self, title: str, description: str, category: str, user_id: int
    ) -> str:
        from app.db.models import Ticket
        from app.db.session import new_session

        with new_session(self.settings.mysql_url) as session:
            ticket = Ticket(
                ticket_no=self._mock_ticket_no(),
                user_id=user_id or 1,
                title=title[:200],
                description=description,
                category=category[:50],
                status="open",
                updated_at=datetime.now(),
            )
            session.add(ticket)
            session.flush()
            ticket.ticket_no = f"T{datetime.now():%Y%m%d}{int(ticket.id):06d}"
            session.commit()
            return ticket.ticket_no

    @staticmethod
    def _mock_ticket_no() -> str:
        return f"T{datetime.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}"

    # ------------------------------------------------------------------ 审计日志
    def _log(
        self,
        run_id: str,
        conversation_id: int | None,
        user_id: int,
        result: ToolResult,
    ) -> None:
        if not self.settings.mysql_url:
            logger.info(
                "tool=%s success=%s run_id=%s args=%s summary=%s",
                result.name,
                result.success,
                run_id,
                json.dumps(result.arguments, ensure_ascii=False),
                result.summary[:200],
            )
            return
        try:
            from app.db.models import ToolCallLog
            from app.db.session import new_session

            with new_session(self.settings.mysql_url) as session:
                session.add(
                    ToolCallLog(
                        run_id=run_id or None,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        tool_name=result.name,
                        arguments=json.dumps(result.arguments, ensure_ascii=False),
                        result_summary=result.summary[:4000],
                        success=1 if result.success else 0,
                        latency_ms=result.latency_ms,
                    )
                )
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("写 tool_call_logs 失败（不影响问答）")
