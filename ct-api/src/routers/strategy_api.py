"""전략/리스크 라우터 — 패턴 판정, 가중치 갱신, 캐시 동기화, 리스크 체크."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from utils.deps import get_db

router = APIRouter(tags=["strategy"])


class EvalPatternsBody(BaseModel):
    pattern_markets: list[str] | None = None


@router.post("/api/strategy/evaluate-patterns")
async def evaluate_patterns(
    body: EvalPatternsBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from services.pattern_service import evaluate_pending_signals

    return await evaluate_pending_signals(db, body.pattern_markets)


@router.post("/api/strategy/update-weights")
async def update_weights(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from core.strategy.tuning.weight_tuning import update_indicator_weights

    return await update_indicator_weights(db)


@router.post("/api/strategy/sync-pattern-cache")
async def sync_pattern_cache_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from sqlalchemy import select

    from core.strategy.tuning.pattern_cache import sync_pattern_cache
    from models.pattern_config import PatternConfig

    await sync_pattern_cache(db)
    rows = (await db.execute(select(PatternConfig))).scalars().all()
    active = sum(1 for r in rows if r.active)
    return {"active": active, "total": len(rows)}


@router.post("/api/strategy/sync-topdown-weights")
async def sync_topdown_weights_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from core.strategy.topdown_tables import sync_topdown_weights

    applied = await sync_topdown_weights(db)
    return {"applied": applied}


class RiskCheckBody(BaseModel):
    side: str
    market: str
    krw_balance: float
    intended_amount_krw: float


@router.post("/api/risk/check")
async def risk_check(
    body: RiskCheckBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import func, select

    from core.strategy.executor.risk_manager import check_risk
    from models.trade import Trade

    settings = request.app.state.settings

    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    daily_count = (await db.execute(
        select(func.count(Trade.id)).where(Trade.created_at >= today_start)
    )).scalar_one()

    result = await check_risk(
        settings,
        side=body.side,
        market=body.market,
        krw_balance=body.krw_balance,
        intended_amount_krw=body.intended_amount_krw,
        daily_trade_count=daily_count,
    )
    return {
        "allowed": result.allowed,
        "reason": result.reason,
        "max_amount_krw": result.max_amount_krw,
    }
