"""寝室报修服务：申请 → 受理 → 完成，学生可取消。

设计（docs/campus-modules.md 第 2 节）：
- **独立于 tickets**：状态机、限流规则、字段都不同，复用会让 tickets 承担两个职责；
- **限流两层**：① 同一用户每自然日最多 `repair_per_user_per_day` 条；
  ② 同一用户 + 同楼栋 + 同房间 + 同类别，`repair_duplicate_hours` 窗口内已有任意状态
  的申请即判重复（409）——防止"刚提交又怕没收到"式重复轰炸；
- **双实现**：有 MYSQL_URL 走 repair_requests 表；否则进程内 dict（本地演示 + 单测）。
  与 notices/tickets 保持同一风格。

状态机：pending（待受理）→ processing（处理中）→ completed（已完成）；
pending / processing 可 → closed（学生取消或管理员关闭）。终态不可再流转。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from app.core.config import Settings

# 分类编码 → 中文名
REPAIR_CATEGORIES: dict[str, str] = {
    "water_power": "水电",
    "network": "网络",
    "furniture": "家具",
    "door_window": "门窗",
    "other": "其他",
}
DEFAULT_CATEGORY = "other"

# 允许的状态流转：key 为当前状态，值为允许到达的状态集合
REPAIR_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"processing", "closed"},
    "processing": {"completed", "closed"},
    "completed": set(),
    "closed": set(),
}
ACTIVE_REPAIR_STATUSES = ("pending", "processing")


class RepairRuleError(ValueError):
    """业务规则不满足（重复申请 / 频率超限 / 非法流转），HTTP 层映射为 4xx。"""


def category_label(code: str | None) -> str:
    return REPAIR_CATEGORIES.get((code or "").strip().lower(), REPAIR_CATEGORIES[DEFAULT_CATEGORY])


# ---------------------------------------------------------------------------
# 内存实现（无 MYSQL_URL 时使用）
# ---------------------------------------------------------------------------
_mock_lock = threading.Lock()
_mock_seq = 0
_mock_repairs: list[dict[str, Any]] = []


def clear_mock_repairs() -> None:
    global _mock_seq
    with _mock_lock:
        _mock_repairs.clear()
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


def _decorate(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["category_label"] = category_label(item.get("category"))
    for key in ("created_at", "updated_at"):
        if key in item:
            item[key] = _fmt(item[key])
    return item


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _clean_text(value: Any, max_len: int) -> str:
    return str(value or "").strip()[:max_len]


def validate_payload(payload: dict[str, Any]) -> dict[str, str]:
    """清洗并校验创建参数；非法输入抛 RepairRuleError。"""
    data = {
        "title": _clean_text(payload.get("title"), 200),
        "description": _clean_text(payload.get("description"), 4000),
        "category": str(payload.get("category") or DEFAULT_CATEGORY).strip().lower(),
        "building": _clean_text(payload.get("building"), 100),
        "room": _clean_text(payload.get("room"), 50),
        "contact": _clean_text(payload.get("contact"), 100),
    }
    if not data["title"]:
        raise RepairRuleError("标题不能为空")
    if len(data["description"]) < 5:
        raise RepairRuleError("故障描述太短，请至少写 5 个字")
    if data["category"] not in REPAIR_CATEGORIES:
        raise RepairRuleError(f"分类必须是 {', '.join(REPAIR_CATEGORIES)} 之一")
    if not data["building"] or not data["room"]:
        raise RepairRuleError("楼栋和房间号不能为空")
    if not data["contact"]:
        raise RepairRuleError("联系方式不能为空，方便维修人员联系")
    return data


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def _limit_config(settings: Settings) -> tuple[int, int]:
    """限流参数（每日上限、判重窗口小时数），提交与预检共用。"""
    per_day = max(1, int(getattr(settings, "repair_per_user_per_day", 3)))
    dup_hours = max(0, int(getattr(settings, "repair_duplicate_hours", 24)))
    return per_day, dup_hours


def precheck_limits(settings: Settings, user_id: int, data: dict[str, str]) -> None:
    """只读预检限流与判重，供 Agent 生成报修草稿时提前拦截。

    与 create_repair 使用同一套规则；预检通过不代表一定能提交成功，
    正式提交时 create_repair 会在写事务内再校验一次（并发下以提交结果为准）。
    """
    now = datetime.now()
    per_day, dup_hours = _limit_config(settings)

    if not settings.mysql_url:
        with _mock_lock:
            _check_limits_mock(user_id, data, now, per_day, dup_hours)
        return

    from app.db.models import RepairRequest
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        recent_count = (
            session.query(RepairRequest)
            .filter(
                RepairRequest.user_id == user_id,
                RepairRequest.created_at >= now - timedelta(days=1),
            )
            .count()
        )
        if recent_count >= per_day:
            raise RepairRuleError(
                f"今日已提交 {recent_count} 条报修，每日最多 {per_day} 条，请明天再试"
            )
        duplicate = (
            session.query(RepairRequest)
            .filter(
                RepairRequest.user_id == user_id,
                RepairRequest.building == data["building"],
                RepairRequest.room == data["room"],
                RepairRequest.category == data["category"],
                RepairRequest.created_at >= now - timedelta(hours=dup_hours),
            )
            .first()
        )
        if duplicate is not None:
            raise RepairRuleError(
                f"{dup_hours} 小时内已提交过同房间同类型的报修（#{duplicate.id}），"
                "请等待处理，不要重复提交"
            )


def create_repair(
    settings: Settings,
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """提交报修。触发限流（429）/重复判定（409）时抛 RepairRuleError。"""
    data = validate_payload(payload)
    now = datetime.now()
    per_day, dup_hours = _limit_config(settings)

    if not settings.mysql_url:
        global _mock_seq
        with _mock_lock:
            _check_limits_mock(user_id, data, now, per_day, dup_hours)
            _mock_seq += 1
            row = {
                "id": _mock_seq,
                "user_id": user_id,
                **data,
                "status": "pending",
                "handler_id": None,
                "handler_remark": None,
                "created_at": now,
                "updated_at": None,
            }
            _mock_repairs.append(row)
            return _decorate(dict(row))

    from app.db.models import RepairRequest
    from app.db.session import new_session

    window_start = now - timedelta(days=1)
    dup_cutoff = now - timedelta(hours=dup_hours)
    with new_session(settings.mysql_url) as session:
        query = session.query(RepairRequest).filter(
            RepairRequest.user_id == user_id,
            RepairRequest.created_at >= window_start,
        )
        recent_count = query.count()
        if recent_count >= per_day:
            raise RepairRuleError(
                f"今日已提交 {recent_count} 条报修，每日最多 {per_day} 条，请明天再试"
            )
        duplicate = (
            session.query(RepairRequest)
            .filter(
                RepairRequest.user_id == user_id,
                RepairRequest.building == data["building"],
                RepairRequest.room == data["room"],
                RepairRequest.category == data["category"],
                RepairRequest.created_at >= dup_cutoff,
            )
            .first()
        )
        if duplicate is not None:
            raise RepairRuleError(
                f"{dup_hours} 小时内已提交过同房间同类型的报修（#{duplicate.id}），"
                "请等待处理，不要重复提交"
            )
        row = RepairRequest(user_id=user_id, created_at=now, **data)
        session.add(row)
        session.commit()
        return get_repair(settings, int(row.id), user_id, is_admin=True)  # type: ignore[return-value]


def _check_limits_mock(
    user_id: int,
    data: dict[str, str],
    now: datetime,
    per_day: int,
    dup_hours: int,
) -> None:
    """内存实现的限流与判重（与 MySQL 实现口径一致）。"""
    window_start = now - timedelta(days=1)
    recent = [
        r
        for r in _mock_repairs
        if r["user_id"] == user_id and _parse_dt(r["created_at"]) >= window_start
    ]
    if len(recent) >= per_day:
        raise RepairRuleError(f"今日已提交 {len(recent)} 条报修，每日最多 {per_day} 条，请明天再试")
    dup_cutoff = now - timedelta(hours=dup_hours)
    duplicate = any(
        r["user_id"] == user_id
        and r["building"] == data["building"]
        and r["room"] == data["room"]
        and r["category"] == data["category"]
        and _parse_dt(r["created_at"]) >= dup_cutoff
        for r in _mock_repairs
    )
    if duplicate:
        raise RepairRuleError(
            f"{dup_hours} 小时内已提交过同房间同类型的报修，请等待处理，不要重复提交"
        )


def list_repairs(
    settings: Settings,
    *,
    user_id: int,
    is_admin: bool,
    status: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """学生看自己的申请，管理员看全部。"""
    limit = max(1, min(int(limit or 50), 200))
    fetch: Callable[[], list[dict[str, Any]]]

    if not settings.mysql_url:
        with _mock_lock:
            rows = [
                dict(r)
                for r in _mock_repairs
                if (is_admin or r["user_id"] == user_id)
                and (not status or r["status"] == status)
            ]
        rows.sort(key=lambda r: (_parse_dt(r["created_at"]) or datetime.min, r["id"]), reverse=True)
        return [_decorate(r) for r in rows[:limit]]

    from app.db.models import RepairRequest
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        query = session.query(RepairRequest)
        if not is_admin:
            query = query.filter(RepairRequest.user_id == user_id)
        if status:
            query = query.filter(RepairRequest.status == status)
        rows = (
            query.order_by(RepairRequest.created_at.desc(), RepairRequest.id.desc())
            .limit(limit)
            .all()
        )
        return [
            _decorate(
                {
                    "id": r.id,
                    "user_id": r.user_id,
                    "title": r.title,
                    "description": r.description,
                    "category": r.category,
                    "building": r.building,
                    "room": r.room,
                    "contact": r.contact,
                    "status": r.status,
                    "handler_id": r.handler_id,
                    "handler_remark": r.handler_remark,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
            )
            for r in rows
        ]


def get_repair(
    settings: Settings,
    repair_id: int,
    user_id: int,
    is_admin: bool,
) -> dict[str, Any] | None:
    """详情：学生只能看自己的（看不到别人的返回 None，避免暴露存在性）。"""
    if not settings.mysql_url:
        with _mock_lock:
            row = next((dict(r) for r in _mock_repairs if r["id"] == repair_id), None)
        if row is None:
            return None
        if not is_admin and row["user_id"] != user_id:
            return None
        return _decorate(row)

    from app.db.models import RepairRequest
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(RepairRequest, repair_id)
        if r is None:
            return None
        if not is_admin and r.user_id != user_id:
            return None
        return _decorate(
            {
                "id": r.id,
                "user_id": r.user_id,
                "title": r.title,
                "description": r.description,
                "category": r.category,
                "building": r.building,
                "room": r.room,
                "contact": r.contact,
                "status": r.status,
                "handler_id": r.handler_id,
                "handler_remark": r.handler_remark,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
        )


def transition_repair(
    settings: Settings,
    repair_id: int,
    actor_id: int,
    *,
    is_admin: bool,
    action: str,
    remark: str = "",
) -> dict[str, Any]:
    """状态流转。

    action ∈ {process, complete, close, cancel}：
    - 管理员：process / complete / close（任意阶段可关单，需附原因）；
    - 学生：仅能 cancel 自己 pending 的申请（还没被受理前）。
    非法流转抛 RepairRuleError（HTTP 400），目标条目不存在/无权 → None（HTTP 404）。
    """
    allowed_actions: dict[str, dict[str, Any]] = {
        "process": {"from": "pending", "to": "processing", "admin_only": True},
        "complete": {"from": "processing", "to": "completed", "admin_only": True},
        "close": {"from": None, "to": "closed", "admin_only": True},   # pending/processing 均可
        "cancel": {"from": "pending", "to": "closed", "admin_only": False},
    }
    if action not in allowed_actions:
        raise RepairRuleError(f"不支持的操作：{action}")
    spec = allowed_actions[action]
    if spec["admin_only"] and not is_admin:
        raise RepairRuleError("仅管理员可执行该操作")
    if action == "close" and not remark.strip():
        raise RepairRuleError("关闭工单必须填写原因")

    now = datetime.now()
    if not settings.mysql_url:
        with _mock_lock:
            row = next((r for r in _mock_repairs if r["id"] == repair_id), None)
            if row is None:
                return None  # type: ignore[return-value]
            if not is_admin and row["user_id"] != actor_id:
                return None  # type: ignore[return-value]
            _check_transition(row, spec, action)
            row["status"] = spec["to"]
            row["handler_id"] = actor_id if is_admin else row["handler_id"]
            if remark.strip():
                row["handler_remark"] = remark.strip()[:4000]
            row["updated_at"] = now
            return _decorate(dict(row))

    from app.db.models import RepairRequest
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(RepairRequest, repair_id)
        if r is None:
            return None
        if not is_admin and r.user_id != actor_id:
            return None
        probe = {
            "status": r.status,
            "user_id": r.user_id,
        }
        _check_transition(probe, spec, action)
        r.status = spec["to"]
        if is_admin:
            r.handler_id = actor_id
        if remark.strip():
            r.handler_remark = remark.strip()[:4000]
        r.updated_at = now
        session.commit()
        return get_repair(settings, repair_id, actor_id, is_admin=True)  # type: ignore[return-value]


def _check_transition(row: dict[str, Any], spec: dict[str, Any], action: str) -> None:
    status = row["status"]
    if spec["from"] is not None and status != spec["from"]:
        raise RepairRuleError(
            f"当前状态为 {status}，不能执行 {action}（要求状态为 {spec['from']}）"
        )
    if spec["from"] is None and status not in ACTIVE_REPAIR_STATUSES:
        raise RepairRuleError(f"当前状态为 {status}，已结束的工单不能再{action}")


def repair_no() -> str:
    """给问答工具预留的工单号生成器（本模块暂未启用，扩展见文档 7 节）。"""
    return f"R{datetime.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}"
