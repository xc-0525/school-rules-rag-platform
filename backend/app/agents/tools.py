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
    "query_my_lost_found",
    "create_reservation_draft",
    "create_lost_found_draft",
    "create_repair_draft",
    "cancel_reservation_draft",
    "resolve_lost_found_draft",
)

# 写操作只生成"待确认草稿"：前端按 action 调对应 POST 接口，模型不直接落库
CAMPUS_DRAFT_TOOL_NAMES = (
    "create_reservation_draft",
    "create_lost_found_draft",
    "create_repair_draft",
    "cancel_reservation_draft",
    "resolve_lost_found_draft",
)

# 失物招领状态的中文展示（仅用于工具摘要；服务层不返回 label，避免与前端 i18n 耦合）
LOSTFOUND_STATUS_LABELS = {
    "pending_review": "待审核",
    "published": "已公示",
    "rejected": "未通过",
    "resolved": "已解决",
    "removed": "已下架",
}

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

# 场馆别名：库里的正式名与学生口语不一致（库里“研讨间”，学生说“研讨室/讨论室”）。
# 精确子串匹配不到时按这张表归一，避免把"有位置"答成"没有这个位置"。
LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "研讨室": ("研讨间", "讨论室"),
    "讨论室": ("研讨间", "研讨室"),
    "会议室": ("研讨间",),
    "研讨间": ("研讨室", "讨论室"),
    "自习室": ("自习区",),
    "自习区": ("自习室",),
    "阅览室": ("阅览",),
    "电子阅览室": ("电子阅览",),
}


def _normalize_location_text(text: str) -> str:
    """去掉空格与分隔符并转小写，让“图书馆 A 区”与“图书馆a区”可比。"""
    import re

    return re.sub(r"[\s\-_·、,，。]+", "", str(text or "")).lower()


def match_locations(
    locations: list[dict[str, Any]], keyword: str
) -> tuple[list[dict[str, Any]], str]:
    """四层策略匹配位置名，返回（命中位置, 匹配说明）。

    1) 原始子串：用户说的正是库里的叫法；
    2) 别名归一：研讨室 → 研讨间（学生口语与正式名不一致）；
    3) 去空格小写：'图书馆a区' → '图书馆一楼自习区a区'；
    4) 二元组重合：拆碎后仍能对上（'图书馆a区座位' vs 正式名命中 图书/书馆/a区）。

    全部落空返回空列表，调用方负责把可选位置清单回给模型，让它可以纠正或告知用户。
    """
    keyword = str(keyword or "").strip()
    if not keyword:
        return list(locations), ""
    names = [str(loc.get("name") or "") for loc in locations]

    hits = [loc for loc, name in zip(locations, names) if keyword in name]
    if hits:
        return hits, ""

    for alias in LOCATION_ALIASES.get(keyword, ()):
        hits = [loc for loc, name in zip(locations, names) if alias in name]
        if hits:
            return hits, f"按近义名称「{alias}」匹配"

    flat_keyword = _normalize_location_text(keyword)
    if flat_keyword:
        hits = [
            loc
            for loc, name in zip(locations, names)
            if flat_keyword in _normalize_location_text(name)
        ]
        if hits:
            return hits, ""

    if len(flat_keyword) >= 2:
        grams = {flat_keyword[i : i + 2] for i in range(len(flat_keyword) - 1)}
        # 至少覆盖一半的关键词片段，且只保留得分最高者：
        # 否则"图书馆a区"会把 4 个都以"图书馆"开头的位置全带出来。
        need = max(2, (len(grams) + 1) // 2)
        scored: list[tuple[int, dict[str, Any]]] = []
        for loc, name in zip(locations, names):
            flat_name = _normalize_location_text(name)
            overlap = sum(1 for gram in grams if gram in flat_name)
            if overlap >= 2:
                scored.append((overlap, loc))
        if scored:
            best = max(score for score, _ in scored)
            if best >= need:
                return [
                    loc for score, loc in sorted(scored, key=lambda item: -item[0])
                    if score == best
                ], "按关键词相近度匹配"

    return [], ""


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
                    "location": {"type": "string", "description": "位置名称关键词，可用用户口语（图书馆/自习区/自习室/研讨室/研讨间），工具会自动做近义匹配"},
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD；必须按系统提示第一行的今天换算（用户说明天就传明天的日期）；不传默认明天"},
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
                "要发布寻物/招领请改用 create_lost_found_draft。"
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
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD；必须按系统提示第一行的今天换算（用户说明天就传明天的日期）；不传默认明天"},
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
    {
        "type": "function",
        "function": {
            "name": "create_lost_found_draft",
            "description": (
                "为当前用户生成失物招领发布草稿（不会直接发布，必须由用户点“确认发布”）。"
                "适用于“我昨天在XX丢了一个学生证，帮我发布失物招领”“我捡到一把钥匙，帮我发个招领”。"
                "kind=lost 表示我丢了东西（寻物），kind=found 表示我捡到东西（招领）。"
                "发布后进入管理员审核（pending_review），审核通过才在公示栏展示。"
                "即使联系方式等信息不全也要先调用本工具：工具会返回缺哪些字段再追问，"
                "严禁自己编造手机号/微信号等参数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["lost", "found"],
                        "description": "lost=我丢了东西，found=我捡到东西",
                    },
                    "title": {"type": "string", "description": "标题，物品名+简短特征，如 学生证/黑色雨伞"},
                    "description": {
                        "type": "string",
                        "description": "详细描述：丢失或拾获的时间、经过、物品特征，至少 10 个字",
                    },
                    "location": {"type": "string", "description": "丢失或拾获地点，如 桂园三楼/图书馆三楼"},
                    "contact": {
                        "type": "string",
                        "description": "联系方式（手机/微信/QQ）；用户没提供就留空，不要编造",
                    },
                },
                "required": ["kind", "title", "description", "location"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_my_lost_found",
            "description": (
                "查询当前用户自己发布的失物招领（含待审核、已发布、被驳回及驳回理由）。"
                "适用于“我发的失物招领审核通过了吗”“我发布过哪些招领”。只读。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["", "pending_review", "published", "rejected", "resolved"],
                        "description": "可选，按状态过滤；留空返回全部",
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
            "name": "create_repair_draft",
            "description": (
                "为当前用户生成寝室报修申请草稿（不会直接提交，必须由用户点“确认提交报修”）。"
                "适用于“宿舍水龙头坏了帮我报修”“12栋305空调不制冷，报个修”。"
                "楼栋、房间号、联系方式必填，缺失时先追问；"
                "即使信息不全也要先调用本工具，由工具返回缺哪些字段再追问；"
                "提交后进入待受理（pending），每日上限与同房间同类型判重会在提交时校验。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "故障标题，如 卫生间水龙头漏水"},
                    "description": {"type": "string", "description": "故障描述，至少 5 个字"},
                    "category": {
                        "type": "string",
                        "enum": ["water_power", "network", "furniture", "door_window", "other"],
                        "description": "分类：水电/网络/家具/门窗/其他",
                    },
                    "building": {"type": "string", "description": "楼栋，如 12栋"},
                    "room": {"type": "string", "description": "房间号，如 305"},
                    "contact": {
                        "type": "string",
                        "description": "联系方式；用户没提供就留空，不要编造",
                    },
                },
                "required": ["title", "description", "category", "building", "room"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reservation_draft",
            "description": (
                "为当前用户生成“取消预约”确认草稿（不会直接取消，必须由用户点“确认取消”）。"
                "适用于“帮我把明天下午的研讨间退了”“取消我最近的预约”。"
                "可先用 query_my_reservations 查到具体预约；有多条时需指定日期或时段。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reservation_id": {"type": "integer", "description": "仅当用户原话明确报出编号时才传；否则用 date/slot 定位或都留空，工具会列出可取消项。禁止使用历史消息里的旧编号"},
                    "date": {"type": "string", "description": "预约日期 YYYY-MM-DD，用于定位"},
                    "slot": {
                        "type": "string",
                        "enum": ["morning", "afternoon", "evening"],
                        "description": "时段，用于定位",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_lost_found_draft",
            "description": (
                "为当前用户生成“标记已找回/已归还”的确认草稿（不会直接改状态，需用户确认）。"
                "适用于“我丢的校园卡找到了，帮我关掉那条寻物启事”。"
                "只能操作本人发布且已发布的条目；可先用 query_my_lost_found 查到编号。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "条目编号（已知时直接传）"},
                    "keyword": {"type": "string", "description": "标题关键词，用于定位条目"},
                },
                "required": [],
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
            elif name == "query_my_lost_found":
                result = await self._query_my_lost_found(arguments, user_id)
            elif name == "create_reservation_draft":
                result = await self._create_reservation_draft(arguments, user_id)
            elif name == "create_lost_found_draft":
                result = await self._create_lost_found_draft(arguments, user_id)
            elif name == "create_repair_draft":
                result = await self._create_repair_draft(arguments, user_id)
            elif name == "cancel_reservation_draft":
                result = await self._cancel_reservation_draft(arguments, user_id)
            elif name == "resolve_lost_found_draft":
                result = await self._resolve_lost_found_draft(arguments, user_id)
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
        match_note = ""
        all_names = [str(loc.get("name") or "") for loc in locations]
        if keyword:
            locations, match_note = match_locations(locations, keyword)
        if not locations:
            available = "、".join(all_names) if all_names else "（系统未配置可预约位置）"
            return ToolResult(
                name="query_location_availability",
                arguments={"location": keyword, "date": res_date},
                success=True,
                summary=(
                    f"没有找到与“{keyword}”匹配的可预约位置。"
                    f"当前可预约位置：{available}。"
                    "请从中选择，或向用户确认到底要哪一个。"
                ),
                payload={"locations": [], "available": all_names},
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
            detail_bits = [
                f"{slot['label']} 余 {slot.get('remaining', 0)}/{slot.get('capacity', 1)}"
                for slot in data["slots"]
            ]
            head = f"{loc.get('name', '')}（{loc.get('zone', '')}）{res_date}"
            if data.get("range_note"):
                lines.append(f"{head}：{data['range_note']}")
            else:
                lines.append(f"{head}：可预约 {('、'.join(free) or '无')}")
                lines.append("  " + "；".join(detail_bits))
            payload.append(
                {
                    "location": loc.get("name"),
                    "date": res_date,
                    "free_slots": free,
                    "slots": data["slots"],
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
            summary=(
                (f"（{match_note}）\n" if match_note else "")
                + "场馆余量查询结果：\n\n"
                + "\n".join(lines)
                + "\n\n如需预约，请告诉我要约的位置、日期和时段（例如“约明天下午的研讨间”），"
                "我可以生成预约草稿，由你确认后生效。"
            ),
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
        # 管理员不占学生座位：直接拒绝，避免生成无法提交的草稿
        if await asyncio.to_thread(self._is_admin_user, user_id):
            return self._draft_error(
                "create_reservation_draft",
                arguments,
                "当前是管理员账号，管理员不参与座位预约；如需替学生处理，可用“代取消”。",
            )
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
        # 只算今天及以后的有效预约，过期记录不占名额（与服务层口径一致）
        today_iso = date.today().isoformat()
        active = [row for row in active if str(row.get("res_date") or "") >= today_iso]
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
            free = [
                f"{item['label']}（余 {item.get('remaining', 0)}）"
                for item in board["slots"]
                if not item["booked"]
            ]
            return self._draft_error(
                "create_reservation_draft",
                arguments,
                f"{location['name']} 在 {target_date.isoformat()} 的{chosen['label']}"
                f" 已约满（{chosen.get('booked_count', 0)}/{chosen.get('capacity', 1)}），"
                + ("可改为：" + "、".join(free) if free else "该日已无空余时段，请换一天。"),
            )

        slot_label = chosen["label"]
        remaining = int(chosen.get("remaining", 1))
        summary = (
            f"已生成预约草稿（尚未占用座位）：\n"
            f"位置：{location['name']}（{location.get('zone', '')}）\n"
            f"时间：{target_date.isoformat()} {slot_label}（当前余 {remaining} 个位置）\n"
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

    # ------------------------------------------------------ 失物招领（发布 / 我的）
    async def _create_lost_found_draft(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        """生成失物招领发布草稿：只做校验与组装，不写库。

        写入由用户在聊天里点“确认发布”，前端调 POST /api/v1/lost-found；
        提交后状态为 pending_review，需管理员审批才公示。
        """
        from app.services import lostfound as lostfound_service

        payload = {
            "kind": arguments.get("kind"),
            "title": arguments.get("title"),
            "description": arguments.get("description"),
            "location": arguments.get("location"),
            "contact": arguments.get("contact"),
        }
        try:
            data = lostfound_service.validate_payload(self.settings, payload)
        except lostfound_service.LostFoundRuleError as exc:
            return self._draft_error(
                "create_lost_found_draft", arguments, f"还不能发布：{exc}。补充后我再生成草稿。"
            )

        kind_label = lostfound_service.LOSTFOUND_KINDS[data["kind"]]
        summary = (
            f"已生成失物招领发布草稿（尚未提交）：\n"
            f"类型：{kind_label}\n"
            f"标题：{data['title']}\n"
            f"地点：{data['location'] or '未填写'}\n"
            f"联系方式：{data['contact']}\n"
            "请点击下方“确认发布”提交；提交后进入管理员审核，审核通过才在公示栏展示。"
        )
        return ToolResult(
            name="create_lost_found_draft",
            arguments={**payload, "kind": data["kind"]},
            success=True,
            summary=summary,
            sources=[
                SourceItem(
                    type="tool",
                    title=f"{kind_label}草稿：{data['title']}",
                    detail=data["location"] or "待补充地点",
                    snippet="待确认，未提交审核",
                )
            ],
            payload={
                "requires_confirmation": True,
                "action": "create_lost_found",
                "title": data["title"],
                "description": data["description"],
                "category": kind_label,
                "kind": data["kind"],
                "location": data["location"],
                "contact": data["contact"],
            },
        )

    async def _query_my_lost_found(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        """查询本人发布的失物招领（含审核状态与驳回理由），只读。"""
        from app.services import lostfound as lostfound_service

        limit = max(1, min(int(arguments.get("limit") or 5), 10))
        status_filter = str(arguments.get("status") or "").strip().lower()
        try:
            rows = await asyncio.to_thread(
                lambda: lostfound_service.list_items(
                    self.settings,
                    user_id=user_id,
                    is_admin=False,
                    scope="mine",
                    limit=30,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("query_my_lost_found failed")
            return ToolResult(
                name="query_my_lost_found",
                arguments=arguments,
                success=False,
                summary=f"查询我的失物招领失败：{exc}",
            )
        if status_filter:
            rows = [row for row in rows if row.get("status") == status_filter]
        rows = rows[:limit]

        if not rows:
            return ToolResult(
                name="query_my_lost_found",
                arguments=arguments,
                success=True,
                summary="你还没有发布过失物招领（或没有该状态下的记录）。"
                "如需发布，告诉我丢了/捡到了什么、在哪里、联系方式。",
                payload={"items": []},
            )
        lines = []
        for row in rows:
            line = (
                f"#{row.get('id')}｜{row.get('kind_label', '')}｜{row.get('title', '')}"
                f"｜{LOSTFOUND_STATUS_LABELS.get(row.get('status', ''), row.get('status', ''))}"
                f"｜{row.get('created_at') or ''}"
            )
            if row.get("status") == "rejected" and row.get("review_remark"):
                line += f"｜驳回理由：{row['review_remark']}"
            lines.append(line)
        return ToolResult(
            name="query_my_lost_found",
            arguments=arguments,
            success=True,
            summary="我发布的失物招领：\n" + "\n".join(lines),
            sources=[
                SourceItem(
                    type="tool",
                    title=row.get("title", "失物信息"),
                    detail=LOSTFOUND_STATUS_LABELS.get(row.get("status", ""), ""),
                    snippet=(row.get("description") or "")[:200],
                )
                for row in rows
            ],
            payload={"items": rows},
        )

    # ------------------------------------------------------------ 寝室报修（提交）
    async def _create_repair_draft(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        """生成报修申请草稿：复用服务层校验与限流预检，不写库。"""
        from app.services import repairs as repair_service

        try:
            data = repair_service.validate_payload(
                {
                    "title": arguments.get("title"),
                    "description": arguments.get("description"),
                    "category": arguments.get("category"),
                    "building": arguments.get("building"),
                    "room": arguments.get("room"),
                    "contact": arguments.get("contact"),
                }
            )
        except repair_service.RepairRuleError as exc:
            return self._draft_error(
                "create_repair_draft", arguments, f"还不能提交报修：{exc}。补充后我再生成草稿。"
            )

        # 限流与判重预检：避免用户点了“确认提交”才被 429/409 拒绝
        try:
            await asyncio.to_thread(
                lambda: repair_service.precheck_limits(self.settings, user_id, data)
            )
        except repair_service.RepairRuleError as exc:
            return self._draft_error("create_repair_draft", arguments, str(exc))
        except Exception as exc:  # noqa: BLE001 —— 预检失败不阻断，交给提交接口兜底
            logger.exception("create_repair_draft precheck failed")
            logger.debug("precheck skipped: %s", exc)

        category_label = repair_service.category_label(data["category"])
        summary = (
            "已生成报修申请草稿（尚未提交）：\n"
            f"故障：{data['title']}（{category_label}）\n"
            f"位置：{data['building']} {data['room']}\n"
            f"联系方式：{data['contact']}\n"
            "请点击下方“确认提交报修”提交；提交后状态为待受理。"
        )
        return ToolResult(
            name="create_repair_draft",
            arguments={**data},
            success=True,
            summary=summary,
            sources=[
                SourceItem(
                    type="tool",
                    title=f"报修草稿：{data['title']}",
                    detail=f"{data['building']} {data['room']}",
                    snippet="待确认，未提交",
                )
            ],
            payload={
                "requires_confirmation": True,
                "action": "create_repair",
                "title": data["title"],
                "description": data["description"],
                "category": category_label,
                "repair_category": data["category"],
                "building": data["building"],
                "room": data["room"],
                "contact": data["contact"],
            },
        )

    # ------------------------------------------------------- 预约（取消 / 失物结案）
    async def _cancel_reservation_draft(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        """生成取消预约草稿：定位到具体预约并校验归属，取消动作由用户确认。"""
        from app.services import reservations as reservation_service

        try:
            active = await asyncio.to_thread(
                lambda: reservation_service.list_reservations(
                    self.settings, user_id=user_id, is_admin=False, status="reserved", limit=20
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("cancel_reservation_draft list failed")
            return self._draft_error(
                "cancel_reservation_draft", arguments, f"暂时无法查询你的预约：{exc}"
            )
        if not active:
            return self._draft_error(
                "cancel_reservation_draft", arguments, "你当前没有可取消的有效预约。"
            )

        target: dict[str, Any] | None = None
        want_id = arguments.get("reservation_id")
        if want_id not in (None, ""):
            try:
                want_id_int = int(want_id)
            except (TypeError, ValueError):
                return self._draft_error(
                    "cancel_reservation_draft", arguments, "预约编号应为数字。"
                )
            target = next((row for row in active if int(row["id"]) == want_id_int), None)
            if target is None:
                return self._draft_error(
                    "cancel_reservation_draft",
                    arguments,
                    f"你的有效预约里没有 #{want_id_int}，可取消的有：{self._reservation_brief(active)}",
                )
        else:
            res_date = str(arguments.get("date") or "").strip()
            slot_raw = str(arguments.get("slot") or "").strip()
            slot = SLOT_ALIASES.get(slot_raw.lower(), SLOT_ALIASES.get(slot_raw, ""))
            candidates = [
                row
                for row in active
                if (not res_date or row.get("res_date") == res_date)
                and (not slot or row.get("slot") == slot)
            ]
            if len(candidates) == 1:
                target = candidates[0]
            elif not candidates:
                return self._draft_error(
                    "cancel_reservation_draft",
                    arguments,
                    f"没找到符合条件的预约，可取消的有：{self._reservation_brief(active)}",
                )
            else:
                return self._draft_error(
                    "cancel_reservation_draft",
                    arguments,
                    f"符合条件的预约不止一条，请指定日期或时段：{self._reservation_brief(candidates)}",
                )

        desc = f"{target.get('res_date', '')} {target.get('slot_label', '')}".strip()
        return ToolResult(
            name="cancel_reservation_draft",
            arguments={"reservation_id": int(target["id"])},
            success=True,
            summary=(
                f"已生成取消预约确认：\n位置：{target.get('location_name', '')}\n时间：{desc}\n"
                "请点击下方“确认取消”完成；取消后该时段会释放给其他同学。"
            ),
            sources=[
                SourceItem(
                    type="tool",
                    title=f"取消预约：{target.get('location_name', '')}",
                    detail=desc,
                    snippet="待确认，尚未取消",
                )
            ],
            payload={
                "requires_confirmation": True,
                "action": "cancel_reservation",
                "title": target.get("location_name", ""),
                "description": desc,
                "category": "图书馆预约",
                "reservation_id": int(target["id"]),
            },
        )

    async def _resolve_lost_found_draft(
        self, arguments: dict[str, Any], user_id: int
    ) -> ToolResult:
        """生成“标记已找回/已归还”草稿：只允许操作本人已发布的条目。"""
        from app.services import lostfound as lostfound_service

        try:
            mine = await asyncio.to_thread(
                lambda: lostfound_service.list_items(
                    self.settings, user_id=user_id, is_admin=False, scope="mine", limit=50
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("resolve_lost_found_draft list failed")
            return self._draft_error(
                "resolve_lost_found_draft", arguments, f"暂时无法查询你的发布：{exc}"
            )

        candidates = [row for row in mine if row.get("status") == "published"]
        if not candidates:
            return self._draft_error(
                "resolve_lost_found_draft",
                arguments,
                "你当前没有“已发布”的失物招领可结案（待审核的条目需先通过审核）。",
            )

        target: dict[str, Any] | None = None
        want_id = arguments.get("item_id")
        if want_id not in (None, ""):
            try:
                want_id_int = int(want_id)
            except (TypeError, ValueError):
                return self._draft_error(
                    "resolve_lost_found_draft", arguments, "条目编号应为数字。"
                )
            target = next((row for row in candidates if int(row["id"]) == want_id_int), None)
            if target is None:
                return self._draft_error(
                    "resolve_lost_found_draft",
                    arguments,
                    f"你没有已发布且编号为 #{want_id_int} 的条目，可结案的有："
                    + self._lostfound_brief(candidates),
                )
        else:
            keyword = str(arguments.get("keyword") or "").strip()
            matched = [row for row in candidates if not keyword or keyword in str(row.get("title", ""))]
            if len(matched) == 1:
                target = matched[0]
            elif not matched:
                return self._draft_error(
                    "resolve_lost_found_draft",
                    arguments,
                    "没找到匹配的已发布条目，可结案的有：" + self._lostfound_brief(candidates),
                )
            else:
                return self._draft_error(
                    "resolve_lost_found_draft",
                    arguments,
                    "匹配到多条，请指定编号：" + self._lostfound_brief(matched),
                )

        return ToolResult(
            name="resolve_lost_found_draft",
            arguments={"item_id": int(target["id"])},
            success=True,
            summary=(
                f"已生成结案确认：\n条目：#{target['id']} {target.get('title', '')}"
                f"（{target.get('kind_label', '')}）\n"
                "请点击下方“确认已找回/已归还”把状态改为已解决；结案后不再出现在公示栏。"
            ),
            sources=[
                SourceItem(
                    type="tool",
                    title=f"结案草稿：{target.get('title', '')}",
                    detail=target.get("kind_label", ""),
                    snippet="待确认，尚未结案",
                )
            ],
            payload={
                "requires_confirmation": True,
                "action": "resolve_lost_found",
                "title": target.get("title", ""),
                "description": target.get("description", "")[:200],
                "category": target.get("kind_label", ""),
                "item_id": int(target["id"]),
            },
        )

    def _is_admin_user(self, user_id: int) -> bool:
        """查一次角色（仅草稿预检用；无库环境视为学生）。"""
        if not getattr(self.settings, "mysql_url", ""):
            return False
        try:
            from app.db.models import User
            from app.db.session import new_session

            with new_session(self.settings.mysql_url) as session:
                row = session.get(User, user_id)
                return bool(row and row.role == "admin")
        except Exception:  # noqa: BLE001 —— 查询失败不阻断，交给提交接口兜底
            logger.exception("check admin role failed")
            return False

    @staticmethod
    def _reservation_brief(rows: list[dict[str, Any]]) -> str:
        return "；".join(
            f"#{row['id']} {row.get('location_name', '')} {row.get('res_date', '')} {row.get('slot_label', '')}"
            for row in rows
        )

    @staticmethod
    def _lostfound_brief(rows: list[dict[str, Any]]) -> str:
        return "；".join(
            f"#{row['id']} {row.get('title', '')}（{row.get('kind_label', '')}）" for row in rows
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
