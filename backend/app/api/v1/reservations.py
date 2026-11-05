"""图书馆位置预约接口。

并发冲突：create 时 SlotConflictError → 409（detail 带冲突说明），其余规则 → 400。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings
from app.schemas.campus import (
    AvailabilityResponse,
    LocationItem,
    ReservationCreate,
    ReservationItem,
)
from app.services import reservations as reservation_service
from app.services.auth import AuthUser, build_current_user_dependency


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/reservations", tags=["图书馆预约"])
    current_user = build_current_user_dependency(settings)

    @router.get("/locations", response_model=list[LocationItem], summary="可预约位置列表")
    async def list_locations_api(user: AuthUser = Depends(current_user)):  # noqa: ARG001
        return await asyncio.to_thread(reservation_service.list_locations, settings)

    @router.get("/availability", response_model=AvailabilityResponse, summary="某位置某天时段占用")
    async def availability_api(
        location_id: int = Query(..., ge=1),
        date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$", alias="date"),
        user: AuthUser = Depends(current_user),  # noqa: ARG001
    ):
        try:
            return await asyncio.to_thread(
                reservation_service.availability,
                settings,
                location_id=location_id,
                res_date=date,
            )
        except reservation_service.ReservationRuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("", response_model=list[ReservationItem], summary="我的预约")
    async def list_my_api(
        status: str = Query(default="", description="reserved/cancelled/fulfilled/no_show"),
        limit: int = Query(default=50, ge=1, le=200),
        user: AuthUser = Depends(current_user),
    ):
        return await asyncio.to_thread(
            reservation_service.list_reservations,
            settings,
            user_id=user.id,
            is_admin=user.is_admin,
            status=status,
            limit=limit,
        )

    @router.post(
        "", response_model=ReservationItem, status_code=201, summary="创建预约（并发安全）"
    )
    async def create_api(payload: ReservationCreate, user: AuthUser = Depends(current_user)):
        try:
            return await asyncio.to_thread(
                reservation_service.create_reservation,
                settings,
                user.id,
                location_id=payload.location_id,
                res_date=payload.res_date,
                slot=payload.slot,
            )
        except reservation_service.SlotConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc) or "手慢了，该时段刚被别人约满，请换一个时段或位置",
                    "conflict_reservation_id": exc.reservation_id,
                },
            ) from exc
        except reservation_service.ReservationRuleError as exc:
            text = str(exc)
            status_code = 429 if "最多同时保留" in text else 400
            raise HTTPException(status_code=status_code, detail=text) from exc

    @router.post(
        "/{reservation_id}/{action}",
        response_model=ReservationItem,
        summary="预约流转（cancel=本人/管理员，fulfill、no_show=管理员）",
    )
    async def transition_api(
        reservation_id: int, action: str, user: AuthUser = Depends(current_user)
    ):
        try:
            item = await asyncio.to_thread(
                reservation_service.transition_reservation,
                settings,
                reservation_id,
                user.id,
                is_admin=user.is_admin,
                action=action,
            )
        except reservation_service.ReservationRuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="预约不存在")
        return item

    return router
