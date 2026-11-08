"""运营看板模型（N-24）。"""

from __future__ import annotations

from pydantic import BaseModel


class TrendPoint(BaseModel):
    date: str
    questions: int
    refused: int


class TopUnmatchedItem(BaseModel):
    query_text: str
    count: int
    last_seen: str | None = None


class LowRatedItem(BaseModel):
    id: int
    question: str | None = None
    comment: str | None = None
    created_at: str | None = None


class OverviewResponse(BaseModel):
    generated_at: str
    days: int
    scale: dict[str, float | int | None]
    quality: dict[str, float | int | None]
    efficiency: dict[str, float | int | None]
    trend: list[TrendPoint]
    top_unmatched: list[TopUnmatchedItem]
    low_rated: list[LowRatedItem]
