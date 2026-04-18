"""Signal 직렬화 헬퍼 — signals.py에서 import."""

from __future__ import annotations

from typing import Any

from models.signal import Signal


def _to_dict(r: Signal) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "user_id": r.user_id,
        "market": r.market,
        "timeframe": r.timeframe,
        "signal_type": r.signal_type,
        "confidence": r.confidence,
        "indicators": r.indicators,
        "source": r.source,
        "acted_on": r.acted_on,
        "verified": r.verified,
        "version": r.version,
        "entry_price": r.entry_price,
        "exit_price": r.exit_price,
        "mfe": r.mfe,
        "mae": r.mae,
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "quality_score": r.quality_score,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }
