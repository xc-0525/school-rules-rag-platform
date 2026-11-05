"""Agent 可调用的真实业务工具（Tool Calling）。

三个 Tool 都对应真实能力，不是占位：
- search_rules：复用 Qdrant 向量检索 + Neo4j 图谱召回；
- query_announcements：真实查询 MySQL announcements 表；
- create_ticket：真实写 MySQL tickets 表并返回工单号。

工具调用会写 tool_call_logs（如果 MySQL 已初始化），用于 Agent 可观测性与答辩演示。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.schemas.chat import Evidence, SourceItem
from app.services.tickets import create_ticket as create_ticket_service

logger = logging.getLogger("app.tools")

# 明确表达"要人工介入"的说法；命中才允许真正写工单。
# 注意：不要把"报修""失物"这类名词短语放进来——"我的报修到哪了"是查询，
# 命中后会误触发 create_ticket 建单（查询类走 query_repair_status）。
TICKET_INTENT_KEYWORDS = (
    "投诉", "工单", "转人工", "人工", "反馈", "举报", "建议",
    "故障", "坏了", "噪音", "扰民", "太吵", "维修", "挂失",
    "我要报修", "申请报修", "提交报修",
)


def _has_ticket_intent(query: str) -> bool:
    text = (query or "").strip()
    return any(keyword in text for keyword in TICKET_INTENT_KEYWORDS)

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


CAMPUS_TOOL_NAMES = (
    "query_repair_status",
    "query_my_reservations",
    "query_location_availability",
    "query_lost_found",
    "create_reservation_draft",
)

# 写操作只生成"待确认草稿"，由前端确认按钮落到 POST /api/v1/reservations
CAMPUS_DRAFT_TOOL_NAMES = ("create_reservation_draft",)

SLOT_ALIASES = {
    "上午": "morning",
    "早上": "morning",
    "下午": "afternoon",
    "中午": "afternoon",
    "晚上": "evening",
    "晚间": "evening",
    "morning": "morning",
    "afternoon": "afternoon",
    "evening": "evening",
}

CAMPUS_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_repair_status",
            "description": (
                "查询当前登录用户本人的寝室报修进度（只读，不会提交或修改任何工单）。"
                "适用于“我报的修到哪一步了”“上周报修处理了吗”这类问自己业务状态的问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["", "pending", "processing", "completed", "closed"],
                        "description": "可选，按状态过滤；留空返回最近全部",
                    },
                    "limit": {"type": "integer", "description": "返回条数，1-10，默认 5"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_my_reservations",
            "description": (
                "查询当前用户本人的图书馆座位/场馆预约记录（只读）。"
                "只回答“我约了什么”，不负责查某个时段有没有空位。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["", "reserved", "cancelled", "fulfilled", "no_show"],
                        "description": "可选，按状态过滤；留空返回最近全部",
                    },
                    "limit": {"type": "integer", "description": "返回条数，1-10，默认 5"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_location_availability",
            "description": (
                "查询某场馆/自习区指定日期的时段余量（只读）。"
                "适用于“明天下午图书馆还有位置吗”。仅支持明天起 7 天内。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "位置名称关键词，如 图书馆/自习室"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD；不传默认明天"},
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_lost_found",
            "description": (
                "检索失物招领公示栏（只读，仅返回已审核发布的信息）。"
                "适用于“有人捡到校园卡吗”“最近有没有人捡到钥匙”。"
                "不能发布、修改或认领，发布失物请引导用户到失物招领页面。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "物品或地点关键词，如 校园卡/钥匙/图书馆"},
                    "kind": {
                        "type": "string",
                        "enum": ["", "lost", "found"],
                        "description": "lost=寻物，found=招领，留空不限",
                    },
                    "limit": {"type": "integer", "description": "返回条数，1-10，默认 5"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reservation_draft",
            "description": (
                "为当前用户生成图书馆/场馆预约草稿（不会真正预约，必须由用户在界面上点“确认预约”）。"
                "适用于“帮我约明天下午的研讨间”“订一个明天上午的自习座位”。"
                "调用前如不确定位置或时段，可先用 query_location_availability 查询。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "位置名称关键词，如 图书馆/研讨间/自习"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD；不传默认明天"},
                    "slot": {
                        "type": "string",
                        "enum": ["morning", "afternoon", "evening"],
                        "description": "时段：上午/下午/晚上",
                    },
                },
                "required": ["location", "slot"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_DEFINITIONS.extend(CAMPUS_TOOL_DEFINITIONS)


def active_tool_definitions(settings: Any) -> list[dict[str, Any]]:
    """按开关返回可用工具，供“有/无校园工具”消融实验复用同一份代码。"""
    if getattr(settings, "campus_tools_enabled", True):
        return TOOL_DEFINITIONS
    return [d for d in TOOL_DEFINITIONS if d["function"]["name"] not in CAMPUS_TOOL_NAMES]


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
        query: str = "",
    ) -> ToolResult:
        started = time.perf_counter()
        result: ToolResult
        # 执行层兜底：消融实验关闭校园工具时，即使模型凭提示词残留“幻觉”出工具名，
        # 也不真正执行，保证对照组干净（只靠不下发定义是拦不住的）。
        if name in CAMPUS_TOOL_NAMES and not getattr(self.settings, "campus_tools_enabled", True):
            result = ToolResult(
                name=name,
                arguments=arguments,
                success=False,
                summary="该工具当前未启用（消融实验对照组）。",
            )
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result
        try:
            if name == "search_rules":
                result = await self._search_rules(arguments)
            elif name == "query_announcements":
                result = await self._query_announcements(arguments)
            elif name == "create_ticket":
                result = await self._create_ticket(arguments, user_id, query)
            elif name == "query_repair_status":
                result = await self._query_repair_status(arguments, user_id)
            elif name == "query_my_reservations":
                result = await self._query_my_reservations(arguments, user_id)
            elif name == "query_location_availability":
                result = await self._query_location_availability(arguments)
            elif name == "query_lost_found":
                result = await self._query_lost_found(arguments, user_id)
            elif name == "create_reservation_draft":
                result = await self._create_reservation_draft(arguments, user_id)
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
        # P1-6：写审计日志是同步 DB 调用，必须丢线程池，否则每次工具调用
        # 都会阻塞事件循环几毫秒到几十毫秒（并发时被放大）。
        await asyncio.to_thread(
            self._log,
            run_id,
            conversation_id,
            user_id,
            result,
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

    async def _announcements_from_db(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        """查询已发布且未过期的公告。

        统一走 services/notices 的过滤逻辑（含有效期），与页面同源——
        避免"页面上已经下架的公告，问答还能答出来"。
        """
        from app.services import notices as notice_service

        try:
            return await asyncio.to_thread(
                lambda: notice_service.list_notices(
                    self.settings, keyword=keyword, limit=limit
                )
            )
        except Exception:  # noqa: BLE001 —— 表未初始化时不影响问答主流程
            logger.exception("query_announcements failed")
            return []

    async def _create_ticket(
        self, arguments: dict[str, Any], user_id: int, query: str = ""
    ) -> ToolResult:
        title = str(arguments.get("title") or "").strip()
        description = str(arguments.get("description") or "").strip()
        category = str(arguments.get("category") or "").strip() or "其他"
        if not title:
            raise ValueError("create_ticket 缺少 title 参数")
        if not description:
            raise ValueError("create_ticket 缺少 description 参数")

        # P1-7：写操作必须有明确的用户意图，不能"聊到宿舍噪音就自动建单"
        if getattr(self.settings, "ticket_require_intent", True) and not _has_ticket_intent(query):
            draft = (
                f"已整理工单草稿（尚未提交）：{title}\n分类：{category}\n"
                "如确认需要人工跟进，请回复「确认提交工单」，系统会正式提交并返回工单号。"
            )
            return ToolResult(
                name="create_ticket",
                arguments={"title": title, "description": description, "category": category},
                success=True,
                summary=draft,
                sources=[
                    SourceItem(
                        type="tool",
                        title="工单草稿（未提交）",
                        detail=f"分类：{category}",
                        snippet=draft,
                    )
                ],
                payload={
                    "status": "draft",
                    "requires_confirmation": True,
                    # 结构化回传草稿，前端"确认提交"直接用它调 POST /api/v1/tickets
                    "title": title,
                    "description": description,
                    "category": category,
                },
            )

        # 与 POST /api/v1/tickets 共用同一套幂等逻辑（services/tickets.py）
        receipt = await asyncio.to_thread(
            create_ticket_service,
            self.settings,
            user_id,
            title,
            description,
            category,
        )
        ticket_no = receipt.ticket_no

        summary = (
            f"已提交转人工工单：{ticket_no}\n"
            f"标题：{title}\n分类：{category}\n"
            "学工部门会尽快跟进；如需补充材料，请保留工单号。"
        )
        if receipt.reused:
            summary = summary.replace("已提交转人工工单", "已存在相同工单（未重复创建）")
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
            payload={"ticket_no": ticket_no, "status": receipt.status, "reused": receipt.reused},
        )

    # ---------------------------------------------------------- 校园服务只读工具
    async def _query_repair_status(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        """只读本人报修进度。is_admin 固定 False，工具入参没有角色，不开越权口子。"""
        from app.services import repairs as repair_service

        status = str(arguments.get("status") or "").strip()
        if status and status not in {"pending", "processing", "completed", "closed"}:
            status = ""
        limit = max(1, min(int(arguments.get("limit") or 5), 10))
        try:
            rows = await asyncio.to_thread(
                lambda: repair_service.list_repairs(
                    self.settings, user_id=user_id, is_admin=False, status=status, limit=limit
                )
            )
        except Exception:  # noqa: BLE001 —— 表未初始化时降级为空，不中断问答
            logger.exception("query_repair_status failed")
            rows = []

        if not rows:
            summary = "没有查到你的报修记录。" if not status else f"没有处于 {status} 状态的报修记录。"
            return ToolResult(
                name="query_repair_status",
                arguments={"status": status, "limit": limit},
                success=True,
                summary=summary,
                payload={"repairs": []},
            )

        labels = {"pending": "待受理", "processing": "处理中", "completed": "已完成", "closed": "已关闭"}
        lines = []
        for index, row in enumerate(rows, 1):
            line = (
                f"{index}. 报修 #{row.get('id')}"
                f"｜{row.get('title', '')}"
                f"｜{labels.get(row.get('status', ''), row.get('status', ''))}"
                f"｜{row.get('building', '')}{row.get('room', '')}"
                f"｜{row.get('created_at', '')}"
            )
            if row.get("handler_remark"):
                line += f"\n   处理意见：{row['handler_remark']}"
            lines.append(line)

        return ToolResult(
            name="query_repair_status",
            arguments={"status": status, "limit": limit},
            success=True,
            summary="你最近的报修记录：\n\n" + "\n".join(lines),
            sources=[
                SourceItem(
                    type="tool",
                    title=f"报修 #{row.get('id')}",
                    detail=labels.get(row.get("status", ""), row.get("status", "")),
                    snippet=f"{row.get('title', '')} {row.get('description', '')}"[:200],
                )
                for row in rows
            ],
            payload={"repairs": rows},
        )

    async def _query_my_reservations(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        from app.services import reservations as reservation_service

        status = str(arguments.get("status") or "").strip()
        if status and status not in {"reserved", "cancelled", "fulfilled", "no_show"}:
            status = ""
        limit = max(1, min(int(arguments.get("limit") or 5), 10))
        try:
            rows = await asyncio.to_thread(
                lambda: reservation_service.list_reservations(
                    self.settings, user_id=user_id, is_admin=False, status=status, limit=limit
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("query_my_reservations failed")
            rows = []

        if not rows:
            summary = "没有查到你的预约记录。" if not status else f"没有处于 {status} 状态的预约记录。"
            return ToolResult(
                name="query_my_reservations",
                arguments={"status": status, "limit": limit},
                success=True,
                summary=summary,
                payload={"reservations": []},
            )

        labels = {
            "reserved": "已预约",
            "cancelled": "已取消",
            "fulfilled": "已使用",
            "no_show": "违约",
        }
        lines = [
            f"{index}. {row.get('location_name', '')}"
            f"｜{row.get('res_date', '')} {row.get('slot_label', '')}"
            f"｜{labels.get(row.get('status', ''), row.get('status', ''))}"
            for index, row in enumerate(rows, 1)
        ]
        return ToolResult(
            name="query_my_reservations",
            arguments={"status": status, "limit": limit},
            success=True,
            summary="你最近的预约记录：\n\n" + "\n".join(lines),
            sources=[
                SourceItem(
                    type="tool",
                    title=f"预约：{row.get('location_name', '')}",
                    detail=f"{row.get('res_date', '')} {row.get('slot_label', '')}",
                    snippet=labels.get(row.get("status", ""), row.get("status", "")),
                )
                for row in rows
            ],
            payload={"reservations": rows},
        )

    async def _query_location_availability(self, arguments: dict[str, Any]) -> ToolResult:
        from app.services import reservations as reservation_service

        keyword = str(arguments.get("location") or "").strip()
        res_date = str(arguments.get("date") or "").strip() or (
            datetime.now().date() + timedelta(days=1)
        ).isoformat()

        # 日期范围前置校验：availability() 对越界日期是"软失败"（返回 range_note、
        # 时段仍全为 false），直接用会把无效日期当"有位置"回答，误导模型。
        days_ahead = max(1, int(getattr(self.settings, "reservation_days_ahead", 7)))
        start = date.today() + timedelta(days=1)
        end = start + timedelta(days=days_ahead - 1)
        try:
            target_date = date.fromisoformat(res_date)
        except ValueError:
            return ToolResult(
                name="query_location_availability",
                arguments={"location": keyword, "date": res_date},
                success=False,
                summary="日期格式应为 YYYY-MM-DD，例如 2026-11-04。",
                payload={"locations": []},
            )
        if not (start <= target_date <= end):
            return ToolResult(
                name="query_location_availability",
                arguments={"location": keyword, "date": res_date},
                success=True,
                summary=(
                    f"只能查询 {start.isoformat()} 至 {end.isoformat()} 的场馆余量，"
                    f"{res_date} 不在可预约范围内。"
                ),
                payload={"locations": []},
            )

        try:
            locations = await asyncio.to_thread(
                lambda: reservation_service.list_locations(self.settings)
            )
        except Exception:  # noqa: BLE001
            logger.exception("query_location_availability list_locations failed")
            locations = []
        if keyword:
            locations = [loc for loc in locations if keyword in str(loc.get("name", ""))]
        if not locations:
            return ToolResult(
                name="query_location_availability",
                arguments={"location": keyword, "date": res_date},
                success=True,
                summary=f"没有找到名称包含“{keyword}”的可预约位置。",
                payload={"locations": []},
            )

        lines, payload = [], []
        for loc in locations[:3]:
            try:
                data = await asyncio.to_thread(
                    lambda loc=loc: reservation_service.availability(
                        self.settings, location_id=loc["id"], res_date=res_date
                    )
                )
            except Exception:  # noqa: BLE001 —— 日期越界等规则错误不拖垮整次调用
                logger.exception("availability failed: %s", loc.get("name"))
                continue
            free = [slot["label"] for slot in data["slots"] if not slot["booked"]]
            head = f"{loc.get('name', '')}（{loc.get('zone', '')}）{res_date}"
            if data.get("range_note"):
                lines.append(f"{head}：{data['range_note']}")
            else:
                lines.append(f"{head}：可预约 {('、'.join(free) or '无')}")
            payload.append(
                {
                    "location": loc.get("name"),
                    "date": res_date,
                    "free_slots": free,
                    "range_note": data.get("range_note", ""),
                }
            )

        if not payload:
            return ToolResult(
                name="query_location_availability",
                arguments={"location": keyword, "date": res_date},
                success=True,
                summary=f"{res_date} 没有可查的余量信息（日期不在可预约范围内）。",
                payload={"locations": []},
            )

        return ToolResult(
            name="query_location_availability",
            arguments={"location": keyword, "date": res_date},
            success=True,
            summary="场馆余量查询结果：\n\n" + "\n".join(lines),
            sources=[
                SourceItem(
                    type="tool",
                    title=f"余量：{item['location']}",
                    detail=res_date,
                    snippet=("可预约 " + "、".join(item["free_slots"]))
                    if item["free_slots"]
                    else "该日无可预约时段",
                )
                for item in payload
            ],
            payload={"locations": payload},
        )

    async def _query_lost_found(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        from app.services import lostfound as lostfound_service

        keyword = str(arguments.get("keyword") or "").strip()
        kind = str(arguments.get("kind") or "").strip()
        if kind not in {"lost", "found"}:
            kind = ""
        limit = max(1, min(int(arguments.get("limit") or 5), 10))
        try:
            rows = await asyncio.to_thread(
                lambda: lostfound_service.list_items(
                    self.settings,
                    user_id=user_id,
                    is_admin=False,
                    kind=kind,
                    scope="public",       # 只读公示栏：未审核/驳回的绝不外泄
                    limit=min(limit * 4, 50),
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("query_lost_found failed")
            rows = []

        if keyword:  # list_items 无 keyword 参数，在结果内做一次包含匹配
            rows = [
                row
                for row in rows
                if keyword
                in f"{row.get('title', '')}{row.get('description', '')}{row.get('location', '')}"
            ]
        rows = rows[:limit]

        if not rows:
            return ToolResult(
                name="query_lost_found",
                arguments={"keyword": keyword, "kind": kind, "limit": limit},
                success=True,
                summary="失物招领公示栏暂未查到相关信息。",
                payload={"items": []},
            )

        lines = [
            f"{index}. 【{row.get('kind_label', '')}】{row.get('title', '')}"
            f"｜地点：{row.get('location', '')}｜{row.get('created_at', '')}"
            f"\n   {row.get('description', '')[:120]}"
            for index, row in enumerate(rows, 1)
        ]
        return ToolResult(
            name="query_lost_found",
            arguments={"keyword": keyword, "kind": kind, "limit": limit},
            success=True,
            summary="失物招领公示栏检索结果：\n\n" + "\n".join(lines),
            sources=[
                SourceItem(
                    type="tool",
                    title=row.get("title", "失物信息"),
                    detail=row.get("kind_label", ""),
                    snippet=row.get("description", "")[:200],
                )
                for row in rows
            ],
            payload={"items": rows},
        )

    async def _create_reservation_draft(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        """生成预约草稿：只校验 + 组装 payload，不写库。

        真正的写入由用户在聊天里点“确认预约”，前端调 POST /api/v1/reservations；
        这样模型不会把“我想约”变成“已约”（脏数据），也能复用后端的并发/配额校验。
        """
        from app.services import reservations as reservation_service

        keyword = str(arguments.get("location") or "").strip()
        slot_raw = str(arguments.get("slot") or "").strip()
        slot = SLOT_ALIASES.get(slot_raw.lower(), SLOT_ALIASES.get(slot_raw, ""))
        res_date = str(arguments.get("date") or "").strip() or (
            datetime.now().date() + timedelta(days=1)
        ).isoformat()

        if not keyword:
            return self._draft_error("create_reservation_draft", arguments, "请先告诉我要预约哪个位置（如研讨间、自习区）。")
        if not slot:
            return self._draft_error(
                "create_reservation_draft", arguments, "请选择时段：上午、下午或晚上。"
            )

        # 日期范围与格式先校验（availability 对越界日期是软失败）
        days_ahead = max(1, int(getattr(self.settings, "reservation_days_ahead", 7)))
        start = date.today() + timedelta(days=1)
        end = start + timedelta(days=days_ahead - 1)
        try:
            target_date = date.fromisoformat(res_date)
        except ValueError:
            return self._draft_error(
                "create_reservation_draft", arguments, "日期格式应为 YYYY-MM-DD。"
            )
        if not (start <= target_date <= end):
            return self._draft_error(
                "create_reservation_draft",
                arguments,
                f"只能预约 {start.isoformat()} 至 {end.isoformat()} 的时段，请换个日期。",
            )

        try:
            locations = await asyncio.to_thread(
                lambda: reservation_service.list_locations(self.settings)
            )
        except Exception:  # noqa: BLE001
            logger.exception("create_reservation_draft list_locations failed")
            locations = []
        matched = [loc for loc in locations if keyword in str(loc.get("name", ""))]
        if not matched:
            return self._draft_error(
                "create_reservation_draft",
                arguments,
                f"没有找到名称包含“{keyword}”的可预约位置，可用位置："
                + "、".join(str(loc.get("name", "")) for loc in locations[:5]),
            )
        location = matched[0]

        # 配额预检：避免用户点了“确认预约”才被 429 拒绝
        max_active = max(1, int(getattr(self.settings, "reservation_max_active_per_user", 3)))
        try:
            active = await asyncio.to_thread(
                lambda: reservation_service.list_reservations(
                    self.settings, user_id=user_id, is_admin=False, status="reserved", limit=50
                )
            )
        except Exception:  # noqa: BLE001 —— 查不到就当没配额限制，交给确认接口兜底
            logger.exception("create_reservation_draft quota check failed")
            active = []
        if len(active) >= max_active:
            return self._draft_error(
                "create_reservation_draft",
                arguments,
                f"你已有 {len(active)} 个有效预约，最多同时保留 {max_active} 个；"
                "请先取消一个（可问“我预约了哪些”）再预约。",
            )

        try:
            board = await asyncio.to_thread(
                lambda: reservation_service.availability(
                    self.settings, location_id=location["id"], res_date=target_date.isoformat()
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("create_reservation_draft availability failed")
            return self._draft_error(
                "create_reservation_draft", arguments, f"暂时无法查询该时段余量：{exc}"
            )
        slot_map = {item["slot"]: item for item in board["slots"]}
        chosen = slot_map.get(slot)
        if chosen is None:
            return self._draft_error("create_reservation_draft", arguments, "时段参数不合法。")
        if chosen["booked"]:
            free = [item["label"] for item in board["slots"] if not item["booked"]]
            return self._draft_error(
                "create_reservation_draft",
                arguments,
                f"{location['name']} 在 {target_date.isoformat()} 的{chosen['label']}已被约满，"
                + ("可改为：" + "、".join(free) if free else "该日已无空余时段，请换一天。"),
            )

        slot_label = chosen["label"]
        summary = (
            f"已生成预约草稿（尚未占用座位）：\n"
            f"位置：{location['name']}（{location.get('zone', '')}）\n"
            f"时间：{target_date.isoformat()} {slot_label}\n"
            "请点击下方“确认预约”按钮完成预约；不确认不会写入预约记录。"
        )
        return ToolResult(
            name="create_reservation_draft",
            arguments={"location": keyword, "date": target_date.isoformat(), "slot": slot},
            success=True,
            summary=summary,
            sources=[
                SourceItem(
                    type="tool",
                    title=f"预约草稿：{location['name']}",
                    detail=f"{target_date.isoformat()} {slot_label}",
                    snippet="待确认，未写入预约记录",
                )
            ],
            payload={
                "requires_confirmation": True,
                "action": "create_reservation",
                # 复用 tools_node 的草稿去重键（title/description/category）
                "title": location["name"],
                "description": f"{target_date.isoformat()} {slot_label}",
                "category": "图书馆预约",
                "location_id": int(location["id"]),
                "res_date": target_date.isoformat(),
                "slot": slot,
                "slot_label": slot_label,
            },
        )

    def _draft_error(
        self, name: str, arguments: dict[str, Any], message: str
    ) -> ToolResult:
        return ToolResult(
            name=name,
            arguments=arguments,
            success=False,
            summary=message,
        )

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
