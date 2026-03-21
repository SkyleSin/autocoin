from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SignalCreate(BaseModel):
    market: str = Field(..., examples=["KRW-BTC"])
    side: Literal["buy", "sell", "hold"]
    source: str = "opencrawl"
    reason: str | None = None
    payload: dict[str, Any] | None = None


class SignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    source: str
    market: str
    side: str
    reason: str | None
    payload: dict[str, Any] | None
