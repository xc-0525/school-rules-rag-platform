"""报修 / 预约 / 失物招领的请求 / 响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 寝室报修
# ---------------------------------------------------------------------------
RepairCategory = Literal["water_power", "network", "furniture", "door_window", "other"]


class RepairCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="问题标题")
    description: str = Field(..., min_length=5, description="故障描述，至少 5 个字")
    category: RepairCategory = Field(default="other", description="水电/网络/家具/门窗/其他")
    building: str = Field(..., min_length=1, max_length=100, description="楼栋，如 12 栋")
    room: str = Field(..., min_length=1, max_length=50, description="房间号，如 305")
    contact: str = Field(..., min_length=1, max_length=100, description="联系电话 / QQ / 微信")


class RepairItem(BaseModel):
    id: int
    user_id: int
    title: str
    description: str
    category: str
    category_label: str
    building: str
    room: str
    contact: str
    status: str = "pending"
    handler_id: int | None = None
    handler_remark: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class RepairTransition(BaseModel):
    action: Literal["process", "complete", "close", "cancel"]
    remark: str = Field(default="", max_length=4000, description="处理意见；关闭时必填")


# ---------------------------------------------------------------------------
# 图书馆预约
# ---------------------------------------------------------------------------
SlotCode = Literal["morning", "afternoon", "evening"]


class LocationItem(BaseModel):
    id: int
    name: str
    zone: str = ""
    capacity: int = 1
    is_active: bool = True


class ReservationCreate(BaseModel):
    location_id: int = Field(..., ge=1)
    res_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="预约日期 YYYY-MM-DD")
    slot: SlotCode = Field(..., description="morning / afternoon / evening")


class ReservationItem(BaseModel):
    id: int
    location_id: int
    location_name: str = ""
    location_zone: str = ""
    user_id: int
    username: str = ""
    res_date: str
    slot: str
    slot_label: str = ""
    status: str = "reserved"
    # 过期未履约（日期已过且仍为 reserved），前端据此折叠与标记
    is_expired: bool = False
    # 管理员取消时填写的原因，学生在“我的预约”可见
    cancel_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ReservationTransition(BaseModel):
    """流转请求体（仅 cancel 用 remark 记录取消原因）。"""

    remark: str = ""


class AvailabilityResponse(BaseModel):
    location_id: int
    date: str
    slots: list[dict]
    range_note: str = ""


# ---------------------------------------------------------------------------
# 失物招领
# ---------------------------------------------------------------------------
LostFoundKind = Literal["lost", "found"]


class LostFoundCreate(BaseModel):
    kind: LostFoundKind = Field(..., description="lost=我丢了东西 / found=我捡到东西")
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=4000)
    location: str = Field(default="", max_length=200, description="丢失/拾获地点")
    contact: str = Field(..., min_length=1, max_length=100)
    image_urls: list[str] = Field(default_factory=list, description="上传接口返回的图片地址")


class LostFoundItem(BaseModel):
    id: int
    user_id: int
    kind: str
    kind_label: str = ""
    title: str
    description: str
    location: str = ""
    contact: str = ""
    images: list[str] = Field(default_factory=list)
    status: str = "pending_review"
    review_remark: str | None = None
    reviewed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class LostFoundReview(BaseModel):
    action: Literal["approve", "reject", "remove"]
    remark: str = Field(default="", max_length=4000, description="驳回理由；reject 时必填")


class LostFoundImageUploaded(BaseModel):
    url: str
    size_bytes: int
    content_type: str
