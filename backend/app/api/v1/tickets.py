"""工单接口：用户确认草稿后正式提交（P1-7 的二次确认）。

与 Tool Calling 的 `create_ticket` 共用 `services.tickets.create_ticket`，
因此幂等规则一致：同一用户同标题同描述在窗口内只会有一张工单。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings
from app.schemas.ticket import TicketCreateRequest, TicketCreateResponse
from app.services.auth import AuthUser, build_current_user_dependency
from app.services.tickets import create_ticket


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["工单"])
    current_user = build_current_user_dependency(settings)

    @router.post(
        "/tickets",
        response_model=TicketCreateResponse,
        status_code=201,
        summary="确认提交转人工工单（幂等）",
    )
    async def submit_ticket(
        payload: TicketCreateRequest, user: AuthUser = Depends(current_user)
    ) -> Any:
        try:
            receipt = await asyncio.to_thread(
                create_ticket,
                settings,
                user.id,
                payload.title,
                payload.description,
                payload.category,
            )
        except Exception as exc:  # noqa: BLE001 —— 数据库不可用要给明确提示
            raise HTTPException(
                status_code=503, detail=f"工单提交失败：{type(exc).__name__}"
            ) from exc
        return TicketCreateResponse(
            ticket_no=receipt.ticket_no, status=receipt.status, reused=receipt.reused
        )

    return router
