"""登录鉴权 API 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50, examples=["admin"])
    password: str = Field(..., min_length=1, max_length=128, examples=["admin123"])


class UserInfo(BaseModel):
    id: int
    username: str
    nickname: str | None = None
    role: str = "student"


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    user: UserInfo


class LogoutResponse(BaseModel):
    logged_out: bool = True
