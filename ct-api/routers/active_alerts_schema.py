"""활성 알림 라우터 공통 스키마 + 변환."""

from __future__ import annotations

from models.active_alert import ActiveAlert
from pydantic import BaseModel


class MarketBody(BaseModel):
    market: str
    user_id: int | None = None


class CreateBody(BaseModel):
    user_id: int | None = None
    market: str
    timeframe: str
    signal_type: str
    confidence: float
    reasons: str = "[]"
    indicators: dict | None = None
    last_alerted_at: str | None = None


class UpdateBody(BaseModel):
    market: str
    timeframe: str
    user_id: int | None = None
    updates: dict


def to_dict(row: ActiveAlert) -> dict:
    """ActiveAlert → dict 변환."""
    return {
        "id": str(row.id),
        "user_id": row.user_id,
        "market": row.market,
        "timeframe": row.timeframe,
        "signal_type": row.signal_type,
        "confidence": row.confidence,
        "reasons": row.reasons,
        "indicators": row.indicators,
        "last_alerted_at": row.last_alerted_at.isoformat() if row.last_alerted_at else None,
        "acted_at": row.acted_at.isoformat() if row.acted_at else None,
        "passed_at": row.passed_at.isoformat() if row.passed_at else None,
        "passed_reasons": row.passed_reasons,
    }
