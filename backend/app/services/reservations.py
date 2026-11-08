"""图书馆位置预约服务：明天起可约，数据库唯一键兜底防并发超卖。

并发控制（docs/campus-modules.md 第 3 节）：
1. **第一道防线（应用层）**：事务内先 `SELECT ... FOR UPDATE` 锁住该位置
   （锁的是 location 行，写竞争聚集在同一行，锁粒度可控），
   在锁内检查该 (location, date, slot) 是否已有有效预约；
2. **第二道防线（数据库层）**：`UNIQUE KEY uk_slot (location_id, res_date, slot)`
   ——即使两个请求绕过应用层检查（比如不同实例），插入时也只有一个成功；
3. **冲突处理**：捕获 IntegrityError，事务回滚，对外返回 409
   （"该时段刚被别人预约"），并附上当前占用情况，前端提示换时段。

时间规则：只允许预约「明天」起 `reservation_days_ahead` 天内的时段
（服务端本地日期计算，客户端传参不可信）。

内存实现用 threading.Lock 模拟同一防线，保证单测也能覆盖冲突路径。
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings

# 时段编码 → 展示名（固定三段，扩展新时段只需改这张表 + 迁移）
RESERVATION_SLOTS: dict[str, str] = {
    "morning": "上午 08:00-12:00",
    "afternoon": "下午 12:00-17:00",
    "evening": "晚上 17:00-22:00",
}

RESERVATION_TRANSITIONS: dict[str, set[str]] = {
    "reserved": {"fulfilled", "cancelled", "no_show"},
    "fulfilled": set(),
    "cancelled": set(),
    "no_show": set(),
}
ACTIVE_RESERVATION_STATUSES = ("reserved",)


class ReservationRuleError(ValueError):
    """业务规则不满足（日期越界 / 超上限 / 非法流转），HTTP 层映射为 4xx。"""


class SlotConflictError(Exception):
    """目标时段已被占满（HTTP 409）。带占用者预约 id，便于排查。"""

    def __init__(self, reservation_id: int, message: str = "") -> None:
        self.reservation_id = reservation_id
        super().__init__(message or "该时段已被预约")


# ---------------------------------------------------------------------------
# 内存实现
# ---------------------------------------------------------------------------
# RLock：list/create 持锁时需要查位置表（seed_mock_locations 会再次加锁），必须可重入
_mock_lock = threading.RLock()
_mock_loc_seq = 0
_mock_res_seq = 0
_mock_locations: list[dict[str, Any]] = []
_mock_reservations: list[dict[str, Any]] = []


def clear_mock_reservations(*, with_locations: bool = True) -> None:
    global _mock_loc_seq, _mock_res_seq
    with _mock_lock:
        _mock_reservations.clear()
        _mock_res_seq = 0
        if with_locations:
            _mock_locations.clear()
            _mock_loc_seq = 0


def seed_mock_locations() -> list[dict[str, Any]]:
    """空库时放 4 个示例位置，与迁移种子一致。"""
    global _mock_loc_seq
    with _mock_lock:
        if _mock_locations:
            return [dict(loc) for loc in _mock_locations]
        for name, zone, capacity in (
            # 自习区/电子阅览按座位数；研讨间按“整间”预约，容量记 1
            ("图书馆一楼自习区 A 区", "自习区", 40),
            ("图书馆二楼研讨间 201", "研讨间", 1),
            ("图书馆二楼研讨间 202", "研讨间", 1),
            ("图书馆三楼电子阅览区", "电子阅览", 30),
        ):
            _mock_loc_seq += 1
            _mock_locations.append(
                {"id": _mock_loc_seq, "name": name, "zone": zone, "capacity": capacity, "is_active": 1}
            )
        return [dict(loc) for loc in _mock_locations]


def _fmt_dt(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _decorate_reservation(row: dict[str, Any], location: dict[str, Any] | None) -> dict[str, Any]:
    item = dict(row)
    item["slot_label"] = RESERVATION_SLOTS.get(item.get("slot", ""), item.get("slot"))
    if location:
        item["location_name"] = location.get("name", "")
        item["location_zone"] = location.get("zone", "")
    item["created_at"] = _fmt_dt(item.get("created_at"))
    item["updated_at"] = _fmt_dt(item.get("updated_at"))
    # 过期未履约：日期已过且仍是 reserved，前端据此显示“已过期”并可折叠
    res_date = str(item.get("res_date") or "")
    item["is_expired"] = (
        bool(res_date) and res_date < date.today().isoformat()
        and item.get("status") in ACTIVE_RESERVATION_STATUSES
    )
    item.setdefault("username", "")
    item.setdefault("cancel_reason", None)
    return item


def _allowed_res_date_range(settings: Settings, today: date | None = None) -> tuple[date, date]:
    days = max(1, int(getattr(settings, "reservation_days_ahead", 7)))
    start = (today or date.today()) + timedelta(days=1)
    return start, start + timedelta(days=days - 1)


def _validate_target(settings: Settings, res_date: str, slot: str) -> date:
    """日期与时段合法性：格式、范围（明天起）、时段枚举。"""
    try:
        target = date.fromisoformat(str(res_date or "").strip())
    except ValueError as exc:
        raise ReservationRuleError("日期格式应为 YYYY-MM-DD") from exc
    start, end = _allowed_res_date_range(settings)
    if target < start:
        raise ReservationRuleError(f"只能预约明天（{start.isoformat()}）及以后的时段")
    if target > end:
        raise ReservationRuleError(f"最多可预约到 {end.isoformat()}（未来 {end.day - start.day + 1} 天内）")
    slot = str(slot or "").strip().lower()
    if slot not in RESERVATION_SLOTS:
        raise ReservationRuleError(f"时段必须是 {', '.join(RESERVATION_SLOTS)} 之一")
    return target


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def list_locations(settings: Settings, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    if not settings.mysql_url:
        seed_mock_locations()
        with _mock_lock:
            rows = [dict(loc) for loc in _mock_locations if include_inactive or loc["is_active"]]
        return rows

    from app.db.models import ReservableLocation
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        query = session.query(ReservableLocation)
        if not include_inactive:
            query = query.filter(ReservableLocation.is_active == 1)
        return [
            {
                "id": loc.id,
                "name": loc.name,
                "zone": loc.zone,
                "capacity": loc.capacity,
                "is_active": bool(loc.is_active),
            }
            for loc in query.order_by(ReservableLocation.id).all()
        ]


def availability(
    settings: Settings,
    *,
    location_id: int,
    res_date: str,
) -> dict[str, Any]:
    """某位置某天各时段的占用与余量。日期越界返回空原因说明。

    capacity 为位置可容纳人数（自习区 40、研讨间 6 等），remaining = capacity - 有效预约数。
    booked 字段保留为“是否已约满”的布尔值，兼容老前端。
    """
    start, end = _allowed_res_date_range(settings)
    try:
        target = date.fromisoformat(str(res_date or "").strip())
    except ValueError as exc:
        raise ReservationRuleError("日期格式应为 YYYY-MM-DD") from exc

    location = next(
        (loc for loc in list_locations(settings, include_inactive=True) if int(loc["id"]) == int(location_id)),
        None,
    )
    capacity = max(1, int((location or {}).get("capacity") or 1))
    counts: dict[str, int] = {slot: 0 for slot in RESERVATION_SLOTS}
    reason = ""
    if target < start or target > end:
        reason = f"该日期不在可预约范围（{start.isoformat()} ~ {end.isoformat()}）"
    else:
        for row in _list_reservations(settings, all_users=True):
            if (
                row["location_id"] == location_id
                and _iso(row.get("res_date")) == target.isoformat()
                and row["status"] in ACTIVE_RESERVATION_STATUSES
            ):
                counts[row["slot"]] = counts.get(row["slot"], 0) + 1

    slots = []
    for slot, label in RESERVATION_SLOTS.items():
        used = counts.get(slot, 0)
        remaining = max(0, capacity - used)
        slots.append(
            {
                "slot": slot,
                "label": label,
                "capacity": capacity,
                "booked_count": used,
                "remaining": remaining,
                "booked": remaining <= 0,
            }
        )
    return {
        "location_id": location_id,
        "date": target.isoformat(),
        "slots": slots,
        "range_note": reason,
    }


def create_reservation(
    settings: Settings,
    user_id: int,
    *,
    location_id: int,
    res_date: str,
    slot: str,
) -> dict[str, Any]:
    """预约。冲突抛 SlotConflictError（409），规则不满足抛 ReservationRuleError（4xx）。"""
    target = _validate_target(settings, res_date, slot)
    slot = str(slot or "").strip().lower()
    max_active = max(1, int(getattr(settings, "reservation_max_active_per_user", 3)))

    if not settings.mysql_url:
        with _mock_lock:
            _check_user_quota_mock(user_id, max_active)
            _lock_and_insert_mock(user_id, location_id, target, slot)
            row = _mock_reservations[-1]
            return _decorate_reservation(dict(row), _mock_location_by_id(location_id))

    from app.db.models import LocationReservation, ReservableLocation
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        # 位置存在且开放
        location = session.get(ReservableLocation, location_id)
        if location is None or not location.is_active:
            raise ReservationRuleError("位置不存在或暂未开放预约")

        # 用户配额：只统计“今天及以后”的有效预约
        # （过期未履约的记录若继续占配额，用户取消完仍会被判满，实测踩到）
        today_iso = date.today().isoformat()
        active_count = (
            session.query(LocationReservation)
            .filter(
                LocationReservation.user_id == user_id,
                LocationReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                LocationReservation.res_date >= today_iso,
            )
            .count()
        )
        if active_count >= max_active:
            raise ReservationRuleError(
                f"您已有 {active_count} 个有效预约，最多同时保留 {max_active} 个；请先取消或等待使用"
            )

        # 第一道防线：锁位置行，锁内检查目标时段
        # （FOR UPDATE 在 MySQL 上对 location 主键行加排他锁，事务结束释放）
        locked = (
            session.query(ReservableLocation)
            .filter(ReservableLocation.id == location_id)
            .with_for_update()
            .one()
        )
        # 同一用户在同一位置同一时段不重复预约（否则会白占名额）
        mine = (
            session.query(LocationReservation)
            .filter(
                LocationReservation.location_id == location_id,
                LocationReservation.res_date == target.isoformat(),
                LocationReservation.slot == slot,
                LocationReservation.user_id == user_id,
                LocationReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            )
            .first()
        )
        if mine is not None:
            raise SlotConflictError(
                int(mine.id), "你在该时段已经预约过这个位置，无需重复预约"
            )

        # 容量校验：锁内计数，保证“查余量 + 插入”原子（uk_slot 唯一键已按容量模型移除）
        capacity = max(1, int(getattr(locked, "capacity", 1) or 1))
        used = (
            session.query(LocationReservation)
            .filter(
                LocationReservation.location_id == location_id,
                LocationReservation.res_date == target.isoformat(),
                LocationReservation.slot == slot,
                LocationReservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            )
            .count()
        )
        if used >= capacity:
            raise SlotConflictError(
                -1,
                f"{RESERVATION_SLOTS.get(slot, slot)} 已约满（{used}/{capacity}），请换一个时段或位置",
            )

        row = LocationReservation(
            location_id=locked.id,
            user_id=user_id,
            res_date=target.isoformat(),
            slot=slot,
            status="reserved",
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            # 兜底：并发下若计数与插入之间出现异常写入，仍映射为 409 而不是 500
            session.rollback()
            raise SlotConflictError(-1, "该时段已约满，请换一个时段或位置") from exc
        return get_reservation(settings, int(row.id), user_id, is_admin=True)  # type: ignore[return-value]


def _check_user_quota_mock(user_id: int, max_active: int) -> None:
    """配额只算今天及以后的有效预约（与 MySQL 实现口径一致）。"""
    today_iso = date.today().isoformat()
    active = sum(
        1
        for r in _mock_reservations
        if r["user_id"] == user_id
        and r["status"] in ACTIVE_RESERVATION_STATUSES
        and _iso(r.get("res_date")) >= today_iso
    )
    if active >= max_active:
        raise ReservationRuleError(
            f"您已有 {active} 个有效预约，最多同时保留 {max_active} 个；请先取消或等待使用"
        )


def _lock_and_insert_mock(user_id: int, location_id: int, target: date, slot: str) -> None:
    """内存实现：_mock_lock 即临界区，检查与插入不可分割（等价 FOR UPDATE 行锁）。"""
    location = _mock_location_by_id(location_id)
    if location is None or not location.get("is_active"):
        raise ReservationRuleError("位置不存在或暂未开放预约")
    capacity = max(1, int(location.get("capacity") or 1))
    used = 0
    for row in _mock_reservations:
        if (
            row["location_id"] == location_id
            and _iso(row["res_date"]) == target.isoformat()
            and row["slot"] == slot
            and row["status"] in ACTIVE_RESERVATION_STATUSES
        ):
            if row["user_id"] == user_id:
                raise SlotConflictError(
                    int(row["id"]), "你在该时段已经预约过这个位置，无需重复预约"
                )
            used += 1
    if used >= capacity:
        raise SlotConflictError(
            -1,
            f"{RESERVATION_SLOTS.get(slot, slot)} 已约满（{used}/{capacity}），请换一个时段或位置",
        )
    global _mock_res_seq
    _mock_res_seq += 1
    _mock_reservations.append(
        {
            "id": _mock_res_seq,
            "location_id": location_id,
            "user_id": user_id,
            "res_date": target.isoformat(),
            "slot": slot,
            "status": "reserved",
            "created_at": datetime.now(),
            "updated_at": None,
        }
    )


def _mock_location_by_id(location_id: int) -> dict[str, Any] | None:
    seed_mock_locations()
    return next((dict(loc) for loc in _mock_locations if loc["id"] == location_id), None)


def list_reservations(
    settings: Settings,
    *,
    user_id: int,
    is_admin: bool,
    status: str = "",
    limit: int = 50,
    offset: int = 0,
    date_from: str = "",
    date_to: str = "",
) -> list[dict[str, Any]]:
    """M-12：透传 offset / date_from / date_to，默认值保持原有调用行为。"""
    return _list_reservations(
        settings,
        user_id=user_id,
        is_admin=is_admin,
        status=status,
        limit=limit,
        offset=offset,
        date_from=date_from,
        date_to=date_to,
    )


def _list_reservations(
    settings: Settings,
    *,
    user_id: int = 0,
    is_admin: bool = False,
    status: str = "",
    limit: int = 200,
    all_users: bool = False,
    offset: int = 0,
    date_from: str = "",
    date_to: str = "",
) -> list[dict[str, Any]]:
    """M-12：新增 offset / date_from / date_to，默认值保持原有行为，返回结构仍为 list。"""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    date_from = (date_from or "").strip()
    date_to = (date_to or "").strip()

    def _in_range(value: Any) -> bool:
        text = str(value or "")
        if date_from and text < date_from:
            return False
        if date_to and text > date_to:
            return False
        return True

    if not settings.mysql_url:
        seed_mock_locations()
        with _mock_lock:
            rows = [
                dict(r)
                for r in _mock_reservations
                if (all_users or is_admin or r["user_id"] == user_id)
                and (not status or r["status"] == status)
                and _in_range(r.get("res_date"))
            ]
        rows.sort(key=lambda r: (str(r.get("res_date") or ""), int(r.get("id") or 0)), reverse=True)
        return [
            _decorate_reservation(r, _mock_location_by_id(r["location_id"]))
            for r in rows[offset : offset + limit]
        ]

    from app.db.models import LocationReservation, ReservableLocation, User
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        query = (
            session.query(LocationReservation, ReservableLocation, User.username)
            .outerjoin(ReservableLocation, ReservableLocation.id == LocationReservation.location_id)
            .outerjoin(User, User.id == LocationReservation.user_id)
        )
        if not (all_users or is_admin):
            query = query.filter(LocationReservation.user_id == user_id)
        if status:
            query = query.filter(LocationReservation.status == status)
        # M-12：日期范围过滤下推到 SQL；格式已由 API 层 pattern 校验
        if date_from:
            query = query.filter(LocationReservation.res_date >= date_from)
        if date_to:
            query = query.filter(LocationReservation.res_date <= date_to)
        rows = (
            query.order_by(LocationReservation.id.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return [
            _decorate_reservation(
                {
                    "id": r.id,
                    "location_id": r.location_id,
                    "user_id": r.user_id,
                    "username": username or "",
                    "res_date": r.res_date,
                    "slot": r.slot,
                    "status": r.status,
                    "cancel_reason": r.cancel_reason,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                },
                {
                    "name": loc.name if loc else "",
                    "zone": loc.zone if loc else "",
                },
            )
            for r, loc, username in rows
        ]


def get_reservation(
    settings: Settings,
    reservation_id: int,
    user_id: int,
    is_admin: bool,
) -> dict[str, Any] | None:
    rows = _list_reservations(settings, user_id=user_id, is_admin=True, limit=200)
    for row in rows:
        if row["id"] == reservation_id and (is_admin or row["user_id"] == user_id):
            return row
    return None


def transition_reservation(
    settings: Settings,
    reservation_id: int,
    actor_id: int,
    *,
    is_admin: bool,
    action: str,
    remark: str = "",
) -> dict[str, Any]:
    """reserved → cancelled（本人或管理员）/ fulfilled、no_show（管理员）。

    cancel 时 remark 写入 cancel_reason，学生在“我的预约”能看到取消原因。
    """
    allowed = {
        "cancel": {"to": "cancelled", "admin_only": False},
        "fulfill": {"to": "fulfilled", "admin_only": True},
        "no_show": {"to": "no_show", "admin_only": True},
    }
    if action not in allowed:
        raise ReservationRuleError(f"不支持的操作：{action}")
    spec = allowed[action]
    if spec["admin_only"] and not is_admin:
        raise ReservationRuleError("仅管理员可执行该操作")

    if not settings.mysql_url:
        with _mock_lock:
            row = next((r for r in _mock_reservations if r["id"] == reservation_id), None)
            if row is None or (not is_admin and row["user_id"] != actor_id):
                return None  # type: ignore[return-value]
            if row["status"] not in ACTIVE_RESERVATION_STATUSES:
                raise ReservationRuleError(f"当前状态为 {row['status']}，不能再变更")
            row["status"] = spec["to"]
            row["updated_at"] = datetime.now()
            if action == "cancel":
                row["cancel_reason"] = (remark or "").strip()[:200] or None
            return _decorate_reservation(dict(row), _mock_location_by_id(row["location_id"]))

    from app.db.models import LocationReservation
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        r = session.get(LocationReservation, reservation_id)
        if r is None or (not is_admin and r.user_id != actor_id):
            return None
        if r.status not in ACTIVE_RESERVATION_STATUSES:
            raise ReservationRuleError(f"当前状态为 {r.status}，不能再变更")
        r.status = spec["to"]
        r.updated_at = datetime.now()
        if action == "cancel":
            r.cancel_reason = (remark or "").strip()[:200] or None
        session.commit()
        return get_reservation(settings, reservation_id, actor_id, is_admin=True)  # type: ignore[return-value]
