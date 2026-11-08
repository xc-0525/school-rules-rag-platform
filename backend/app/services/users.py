"""用户账号管理（管理员）：列表 / 改资料 / 改角色与状态 / 重置密码。

设计取舍：
- **只改不删**。账号是会话、消息、工单、预约、失物的外键主体，物理删除会让历史
  数据悬空；禁用（status=0）既能立即阻断登录，又保留全部痕迹可追溯。
- **改密/降权/禁用后吊销该用户所有 token**（复用 auth_tokens.revoke_all_for_user），
  否则旧 token 在有效期内仍可访问，等于没改。
- **防自锁**：管理员不能改自己的角色与状态，也不能把最后一个可用管理员降级或禁用，
  避免把系统锁死在无人可管理的状态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import Settings

USER_ROLES: tuple[str, ...] = ("student", "admin")
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128


class UserAdminError(ValueError):
    """用户管理操作被拒绝（带 HTTP 状态码，供接口层直接抛出）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _require_mysql(settings: Settings) -> None:
    if not settings.mysql_url:
        raise UserAdminError(
            400, "用户管理需要连接数据库（当前为内存演示模式，没有用户表）"
        )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """用户行 → 对外结构（绝不带 password）。"""
    return {
        "id": int(row.id),
        "username": str(row.username),
        "nickname": row.nickname or "",
        "role": str(row.role or "student"),
        "status": int(row.status or 0),
        "education_level": str(getattr(row, "education_level", "unset") or "unset"),
        "college": getattr(row, "college", None),
        "grade": getattr(row, "grade", None),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def list_users(
    settings: Settings,
    *,
    keyword: str = "",
    role: str = "",
    status: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """分页查询用户：关键词匹配用户名/昵称，按 id 升序（账号列表用稳定顺序更易读）。"""
    _require_mysql(settings)
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    keyword = (keyword or "").strip()
    role = (role or "").strip()

    from sqlalchemy import func, or_, select

    from app.db.models import User
    from app.db.session import new_session

    with new_session(settings.mysql_url) as session:
        conditions = []
        if keyword:
            like = f"%{keyword}%"
            conditions.append(or_(User.username.like(like), User.nickname.like(like)))
        if role:
            conditions.append(User.role == role)
        if status is not None:
            conditions.append(User.status == int(status))

        count_stmt = select(func.count(User.id))
        page_stmt = select(User)
        for condition in conditions:
            count_stmt = count_stmt.where(condition)
            page_stmt = page_stmt.where(condition)

        total = int(session.execute(count_stmt).scalar() or 0)
        rows = session.execute(
            page_stmt.order_by(User.id.asc()).limit(limit).offset(offset)
        ).scalars().all()
        items = [_row_to_dict(row) for row in rows]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    }


def _active_admin_count(session: Any, exclude_id: int | None = None) -> int:
    """统计"可登录的管理员"数量（用于防自锁判定）。"""
    from sqlalchemy import func, select

    from app.db.models import User

    stmt = select(func.count(User.id)).where(User.role == "admin", User.status == 1)
    if exclude_id is not None:
        stmt = stmt.where(User.id != int(exclude_id))
    return int(session.execute(stmt).scalar() or 0)


def update_user(
    settings: Settings,
    *,
    actor_id: int,
    user_id: int,
    nickname: str | None = None,
    role: str | None = None,
    status: int | None = None,
    education_level: str | None = None,
    college: str | None = None,
    grade: str | None = None,
) -> dict[str, Any]:
    """修改昵称 / 角色 / 状态 / 身份画像。用户名与密码不在此接口（密码走 reset_password）。"""
    _require_mysql(settings)

    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import new_session
    from app.services.profile import ProfileError, normalize_education

    role = role.strip() if isinstance(role, str) and role.strip() else None
    if role is not None and role not in USER_ROLES:
        raise UserAdminError(400, f"角色只能是 {' / '.join(USER_ROLES)}")
    if status is not None and int(status) not in (0, 1):
        raise UserAdminError(400, "状态只能是启用(1)或禁用(0)")
    if nickname is not None:
        nickname = nickname.strip()
        if len(nickname) > 50:
            raise UserAdminError(400, "昵称不能超过 50 个字符")
    if education_level is not None:
        try:
            education_level = normalize_education(education_level)
        except ProfileError as exc:
            raise UserAdminError(400, str(exc)) from exc
    if college is not None and len(college.strip()) > 64:
        raise UserAdminError(400, "学院名称不能超过 64 个字符")
    if grade is not None and len(grade.strip()) > 16:
        raise UserAdminError(400, "年级不能超过 16 个字符")

    with new_session(settings.mysql_url) as session:
        target = session.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
        if target is None:
            raise UserAdminError(404, "用户不存在")

        changing_privilege = (role is not None and role != str(target.role or "")) or (
            status is not None and int(status) != int(target.status or 0)
        )
        if int(user_id) == int(actor_id) and changing_privilege:
            raise UserAdminError(400, "不能修改自己的角色或状态，请让其他管理员操作")

        demoting = role is not None and role != "admin" and str(target.role or "") == "admin"
        disabling = status == 0 and str(target.role or "") == "admin" and int(target.status or 0) == 1
        if (demoting or disabling) and _active_admin_count(session, exclude_id=user_id) == 0:
            raise UserAdminError(409, "这是最后一个可用的管理员，不能降级或禁用")

        if nickname is not None:
            target.nickname = nickname or target.username
        if role is not None:
            target.role = role
        if status is not None:
            target.status = int(status)
        if education_level is not None:
            target.education_level = education_level
        if college is not None:
            target.college = college.strip() or None
        if grade is not None:
            target.grade = grade.strip() or None
        target.updated_at = datetime.now()
        session.commit()
        session.refresh(target)
        payload = _row_to_dict(target)

    if changing_privilege:
        # 降权/禁用后旧 token 必须立刻失效，否则在有效期内仍能越权访问
        from app.services.auth import auth_tokens

        auth_tokens.revoke_all_for_user(int(user_id))
    return payload


def reset_password(
    settings: Settings,
    *,
    actor_id: int,
    user_id: int,
    new_password: str,
) -> dict[str, Any]:
    """重置密码并吊销该用户全部 token（含管理员重置自己）。"""
    _require_mysql(settings)

    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import new_session
    from app.services.auth import auth_tokens

    # M-19：与注册共用策略，避免"注册严、重置松"的绕过口子
    from app.core.password_policy import PasswordPolicyError, validate_password

    password = new_password or ""
    try:
        validate_password(password)
    except PasswordPolicyError as exc:
        raise UserAdminError(400, str(exc)) from exc

    with new_session(settings.mysql_url) as session:
        target = session.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
        if target is None:
            raise UserAdminError(404, "用户不存在")
        target.password = hash_password(password)
        target.updated_at = datetime.now()
        session.commit()
        session.refresh(target)
        payload = _row_to_dict(target)

    auth_tokens.revoke_all_for_user(int(user_id))
    return {**payload, "tokens_revoked": True, "self_reset": int(user_id) == int(actor_id)}
