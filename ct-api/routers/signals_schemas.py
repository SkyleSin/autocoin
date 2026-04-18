"""Signal 라우터 요청 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SignalCreate(BaseModel):
    market: str
    timeframe: str
    signal_type: str
    confidence: float = 0.0
    indicators: dict[str, Any] | None = None
    source: str = "technical"
    user_id: int | None = None
    entry_price: float | None = None
    mfe: float | None = None
    mae: float | None = None
    version: str = "1.0.1"


class SignalUpdate(BaseModel):
    verified: bool | None = None
    mfe: float | None = None
    mae: float | None = None
    exit_price: float | None = None
    closed_at: datetime | None = None
    quality_score: float | None = None
    acted_on: bool | None = None


class CloseOppositeBody(BaseModel):
    market: str
    new_signal_type: str
    exit_price: float
