"""运营看板接口（N-24，仅管理员）。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings
from app.schemas.analytics import OverviewResponse
from app.services import analytics as analytics_service
from app.services.auth import AuthUser, build_current_user_dependency


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["运营看板"])
    current_user = build_current_user_dependency(settings)

    @router.get("/stats/overview", response_model=OverviewResponse, summary="运营看板总览")
    async def overview_api(
        days: int = Query(default=7, ge=1, le=90, description="统计窗口（天）"),
        user: AuthUser = Depends(current_user),
    ) -> Any:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="仅管理员可访问")
        try:
            return await asyncio.to_thread(analytics_service.overview, settings, days)
        except analytics_service.AnalyticsError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
