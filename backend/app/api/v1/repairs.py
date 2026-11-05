"""寝室报修接口：学生提交 / 查看本人，管理员受理与流转。

错误码约定：
- 400 RepairRuleError（非法输入 / 非法流转）
- 404 条目不存在或无权查看（不区分，避免暴露存在性）
- 429 频率超限；409 重复申请（与限流区分开，前端提示不同）
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings
from app.schemas.campus import RepairCreate, RepairItem, RepairTransition
from app.services import repairs as repair_service
from app.services.auth import AuthUser, build_current_user_dependency


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["寝室报修"])
    current_user = build_current_user_dependency(settings)

    def _map_rule_error(exc: repair_service.RepairRuleError, status_code: int) -> HTTPException:
        return HTTPException(status_code=status_code, detail=str(exc))

    @router.get("/repairs", response_model=list[RepairItem], summary="报修列表（学生=本人）")
    async def list_repairs_api(
        status: str = Query(default="", description="状态过滤：pending/processing/completed/closed"),
        limit: int = Query(default=50, ge=1, le=200),
        user: AuthUser = Depends(current_user),
    ):
        return await asyncio.to_thread(
            repair_service.list_repairs,
            settings,
            user_id=user.id,
            is_admin=user.is_admin,
            status=status,
            limit=limit,
        )

    @router.post(
        "/repairs", response_model=RepairItem, status_code=201, summary="提交报修申请"
    )
    async def create_repair_api(payload: RepairCreate, user: AuthUser = Depends(current_user)):
        try:
            return await asyncio.to_thread(
                repair_service.create_repair, settings, user.id, payload.model_dump()
            )
        except repair_service.RepairRuleError as exc:
            text = str(exc)
            if "每日最多" in text:
                raise _map_rule_error(exc, 429) from exc
            if "重复提交" in text or "已提交过" in text:
                raise _map_rule_error(exc, 409) from exc
            raise _map_rule_error(exc, 400) from exc

    @router.get("/repairs/{repair_id}", response_model=RepairItem, summary="报修详情")
    async def get_repair_api(repair_id: int, user: AuthUser = Depends(current_user)):
        item = await asyncio.to_thread(
            repair_service.get_repair, settings, repair_id, user.id, user.is_admin
        )
        if item is None:
            raise HTTPException(status_code=404, detail="报修单不存在")
        return item

    @router.post(
        "/repairs/{repair_id}/transition",
        response_model=RepairItem,
        summary="状态流转（受理/完成/关闭=管理员，取消=本人）",
    )
    async def transition_api(
        repair_id: int, payload: RepairTransition, user: AuthUser = Depends(current_user)
    ):
        try:
            item = await asyncio.to_thread(
                repair_service.transition_repair,
                settings,
                repair_id,
                user.id,
                is_admin=user.is_admin,
                action=payload.action,
                remark=payload.remark,
            )
        except repair_service.RepairRuleError as exc:
            raise _map_rule_error(exc, 400) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="报修单不存在")
        return item

    return router
