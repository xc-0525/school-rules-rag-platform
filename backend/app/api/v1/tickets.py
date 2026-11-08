"""工单接口：草稿确认提交 + 闭环查询与流转（N-01）。

与 Tool Calling 的 `create_ticket` 共用 `services.tickets.create_ticket`，
因此幂等规则一致：同一用户同标题同描述在窗口内只会有一张工单。

权限：学生只看/只读本人；管理员可查看全部并流转。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings
from app.schemas.ticket import (
    TicketCreateRequest,
    TicketCreateResponse,
    TicketItem,
    TicketPage,
    TicketTransitionRequest,
)
from app.services import tickets as ticket_service
from app.services.auth import AuthUser, build_current_user_dependency


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["工单"])
    current_user = build_current_user_dependency(settings)

    def _raise_rule_error(exc: ticket_service.TicketRuleError) -> None:
        status_code = 403 if isinstance(exc, ticket_service.TicketPermissionError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

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
                ticket_service.create_ticket,
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

    @router.get("/tickets", response_model=TicketPage, summary="工单列表（学生=本人，管理员=全部）")
    async def list_tickets_api(
        status: str = Query(default="", description="状态过滤：open/processing/closed"),
        keyword: str = Query(default="", description="工单号/标题/描述关键词"),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        user: AuthUser = Depends(current_user),
    ) -> Any:
        try:
            items, total = await asyncio.to_thread(
                ticket_service.list_tickets,
                settings,
                user_id=user.id,
                is_admin=user.is_admin,
                status=status,
                keyword=keyword,
                limit=limit,
                offset=offset,
            )
        except ticket_service.TicketRuleError as exc:
            _raise_rule_error(exc)
        return TicketPage(items=items, total=total)

    @router.get("/tickets/{ticket_id}", response_model=TicketItem, summary="工单详情")
    async def get_ticket_api(ticket_id: int, user: AuthUser = Depends(current_user)) -> Any:
        item = await asyncio.to_thread(
            ticket_service.get_ticket, settings, ticket_id, user.id, user.is_admin
        )
        if item is None:
            raise HTTPException(status_code=404, detail="工单不存在")
        return item

    @router.post(
        "/tickets/{ticket_id}/transition",
        response_model=TicketItem,
        summary="工单流转（受理/记录进展/关闭，仅管理员）",
    )
    async def transition_ticket_api(
        ticket_id: int,
        payload: TicketTransitionRequest,
        user: AuthUser = Depends(current_user),
    ) -> Any:
        try:
            item = await asyncio.to_thread(
                ticket_service.transition_ticket,
                settings,
                ticket_id,
                user.id,
                is_admin=user.is_admin,
                action=payload.action,
                remark=payload.remark,
            )
        except ticket_service.TicketRuleError as exc:
            _raise_rule_error(exc)
        if item is None:
            raise HTTPException(status_code=404, detail="工单不存在")
        return item

    return router
