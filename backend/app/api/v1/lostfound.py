"""失物招领接口：发布先审后发，图片独立上传。

图片链路：
- POST /lost-found/images（multipart）→ 存 data/lostfound/，返回 /api/v1/lost-found/images/<file>；
- GET  /lost-found/images/<file> → 登录用户只读访问（防遍历：文件名白名单正则）。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.core.config import BASE_DIR, Settings
from app.schemas.campus import LostFoundCreate, LostFoundImageUploaded, LostFoundItem, LostFoundReview
from app.services import lostfound as lostfound_service
from app.services.auth import AuthUser, build_current_user_dependency

try:  # 与 documents 一致：装了 python-multipart 才注册上传接口
    import multipart as _multipart  # noqa: F401

    HAS_MULTIPART = True
except ImportError:
    HAS_MULTIPART = False

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_DIR = BASE_DIR / "data" / "lostfound"
# 文件名白名单：uuid + 单一后缀，杜绝路径穿越
_SAFE_FILENAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/lost-found", tags=["失物招领"])
    current_user = build_current_user_dependency(settings)

    def _map_rule(exc: lostfound_service.LostFoundRuleError, code: int) -> HTTPException:
        return HTTPException(status_code=code, detail=str(exc))

    @router.get("", response_model=list[LostFoundItem], summary="失物招领列表")
    async def list_api(
        kind: str = Query(default="", description="lost/found，留空全部"),
        scope: str = Query(
            default="public", description="public=已发布 / mine=我的全部 / pending=待审（管理员）"
        ),
        older_than_hours: int = Query(default=0, ge=0, description="待审超过 N 小时（审批超时查询）"),
        keyword: str = Query(default="", description="标题/描述/地点关键词（M-13）"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, description="分页偏移（M-13）"),
        user: AuthUser = Depends(current_user),
    ):
        if scope == "pending" and not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可查看待审列表")
        return await asyncio.to_thread(
            lostfound_service.list_items,
            settings,
            user_id=user.id,
            is_admin=user.is_admin,
            kind=kind,
            scope=scope,
            older_than_hours=older_than_hours,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )

    @router.post(
        "", response_model=LostFoundItem, status_code=201, summary="提交发布申请（待审核）"
    )
    async def create_api(payload: LostFoundCreate, user: AuthUser = Depends(current_user)):
        try:
            return await asyncio.to_thread(
                lostfound_service.create_item, settings, user.id, payload.model_dump()
            )
        except lostfound_service.LostFoundRuleError as exc:
            raise _map_rule(exc, 400) from exc

    @router.get("/{item_id}", response_model=LostFoundItem, summary="详情")
    async def get_api(item_id: int, user: AuthUser = Depends(current_user)):
        item = await asyncio.to_thread(
            lostfound_service.get_item, settings, item_id, user.id, user.is_admin
        )
        if item is None:
            raise HTTPException(status_code=404, detail="条目不存在")
        return item

    @router.post(
        "/{item_id}/review", response_model=LostFoundItem, summary="审批（管理员：通过/驳回/下架）"
    )
    async def review_api(item_id: int, payload: LostFoundReview, user: AuthUser = Depends(current_user)):
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可审批")
        try:
            item = await asyncio.to_thread(
                lostfound_service.review_item,
                settings,
                item_id,
                user.id,
                action=payload.action,
                remark=payload.remark,
            )
        except lostfound_service.LostFoundRuleError as exc:
            raise _map_rule(exc, 400) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="条目不存在")
        return item

    @router.post(
        "/{item_id}/resolve", response_model=LostFoundItem, summary="确认找回/归还（发布人或管理员）"
    )
    async def resolve_api(item_id: int, user: AuthUser = Depends(current_user)):
        try:
            item = await asyncio.to_thread(
                lostfound_service.resolve_item,
                settings,
                item_id,
                user.id,
                is_admin=user.is_admin,
            )
        except lostfound_service.LostFoundRuleError as exc:
            raise _map_rule(exc, 400) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="条目不存在")
        return item

    if HAS_MULTIPART:

        @router.post(
            "/images",
            response_model=LostFoundImageUploaded,
            status_code=201,
            summary="上传图片（jpg/png/webp，≤5MB）",
        )
        async def upload_image_api(file: UploadFile, user: AuthUser = Depends(current_user)):  # noqa: ARG001
            max_mb = max(1, int(getattr(settings, "lostfound_image_max_mb", 5)))
            max_bytes = max_mb * 1024 * 1024
            content = await file.read()
            if len(content) == 0:
                raise HTTPException(status_code=400, detail="空文件")
            if len(content) > max_bytes:
                raise HTTPException(status_code=413, detail=f"图片超过 {max_mb}MB 上限")
            suffix = Path(file.filename or "").suffix.lower()
            if suffix == ".jpeg":
                suffix = ".jpg"
            content_type = (file.content_type or "").lower()
            if content_type in ALLOWED_IMAGE_TYPES:
                expected = ALLOWED_IMAGE_TYPES[content_type]
                if suffix and suffix != expected:
                    raise HTTPException(status_code=400, detail="文件后缀与实际内容类型不一致")
                suffix = expected
            elif suffix in ALLOWED_IMAGE_SUFFIXES:
                pass  # content_type 缺失时按后缀放行，浏览器正常上传都会带
            else:
                raise HTTPException(
                    status_code=415,
                    detail="仅支持 jpg / png / webp 图片",
                )
            IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}{suffix}"
            (IMAGE_DIR / filename).write_bytes(content)
            return {
                "url": f"/api/v1/lost-found/images/{filename}",
                "size_bytes": len(content),
                "content_type": content_type or f"image/{suffix.lstrip('.')}",
            }

    @router.get("/images/{filename}", summary="读取图片（登录用户）")
    async def read_image_api(filename: str, user: AuthUser = Depends(current_user)):  # noqa: ARG001
        if not _SAFE_FILENAME.match(filename):
            raise HTTPException(status_code=400, detail="非法的图片地址")
        path = IMAGE_DIR / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="图片不存在")
        media = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }[path.suffix.lower()]
        return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=86400"})

    return router
