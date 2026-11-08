"""登录鉴权 API 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50, examples=["admin"])
    password: str = Field(..., min_length=1, max_length=128, examples=["REPLACE_ME"])


class RegisterRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=3,
        max_length=20,
        pattern=r"^[A-Za-z0-9_]+$",
        examples=["zhangsan"],
        description="3-20 位字母/数字/下划线",
    )
    password: str = Field(..., min_length=6, max_length=128, examples=["pass1234"])
    nickname: str | None = Field(default=None, max_length=50, examples=["张三"])
    # N-20：注册时可选填学历层次，未填默认 unset，登录后引导补全
    education_level: str | None = Field(
        default=None, description="undergraduate=本科 / junior=专科"
    )


class UserInfo(BaseModel):
    id: int
    username: str
    nickname: str | None = None
    role: str = "student"
    # N-20 身份画像
    education_level: str = "unset"
    education_label: str = "未设置"
    college: str | None = None
    grade: str | None = None


class ProfileUpdateRequest(BaseModel):
    """本人补全资料；字段为 None 表示不改。"""

    education_level: str | None = Field(default=None, description="unset/undergraduate/junior")
    college: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=16)


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    user: UserInfo


class LogoutResponse(BaseModel):
    logged_out: bool = True
