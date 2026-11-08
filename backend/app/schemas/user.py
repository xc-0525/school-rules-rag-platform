"""用户账号管理的 API 模型（仅管理员可用）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserItem(BaseModel):
    """用户列表项。绝不包含 password。"""

    id: int
    username: str
    nickname: str = ""
    role: str = "student"
    status: int = 1
    created_at: str | None = None
    updated_at: str | None = None


class UserPage(BaseModel):
    """用户列表分页结果。

    has_more 由 offset + 本页条数 < total 判定，总数恰好整页时不会多请求一次空页。
    """

    items: list[UserItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0
    has_more: bool = False


class UserUpdateRequest(BaseModel):
    """修改用户资料。字段为 None 表示不改；用户名不可改（是登录标识）。"""

    nickname: str | None = None
    role: str | None = None
    status: int | None = None


class ResetPasswordRequest(BaseModel):
    """重置密码（管理员代改，无需旧密码）。"""

    new_password: str = ""
