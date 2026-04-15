"""지표 계산 핸들러 — compute_indicators.

캔들 수집 후 체이닝되어 실행.
DB에서 캔들 로드 → 지표/S/R/패턴 계산 → indicator_snapshots 저장.
분석 TF(ref_only=False)면 strategy_queue에 이벤트 push.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.analysis.indicators import compute_all
from core.analysis.patterns import analyze as analyze_patterns
from core.analysis.support_resistance import analyze as analyze_sr
from models.candle import Candle
from models.indicator_snapshot import IndicatorSnapshot
from models.job import Job
from models.job_status import JobStatus
from utils.config import Settings

logger = logging.getLogger(__name__)

# app.py lifespan에서 세팅. strategy_scheduler와 공유.
_strategy_queue: asyncio.Queue | None = None


def set_strategy_queue(queue: asyncio.Queue) -> None:
    """strategy_queue 주입 (app.py lifespan에서 호출)."""
    global _strategy_queue  # noqa: PLW0603
    _strategy_queue = queue


async def handle_compute_indicators(
    job: Job,
    settings: Settings,
    *,
    session: AsyncSession,
) -> None:
    """캔들 → 지표 계산 → indicator_snapshots 저장."""
    if not job.content or not str(job.content.get("market", "")).strip():
        raise ValueError("compute_indicators 잡에 content.market 이 필요합니다.")

    market = str(job.content["market"]).strip()
    timeframe = str(job.content["timeframe"])
    ref_only = bool(job.content.get("ref_only", False))
    candle_limit = 200

    # DB에서 캔들 로드
    rows = await session.execute(
        select(Candle)
        .where(Candle.market == market, Candle.timeframe == timeframe)
        .order_by(Candle.open_at.desc())
        .limit(candle_limit)
    )
    candles = list(reversed(rows.scalars().all()))

    if len(candles) < 26:
        job.response = {
            "status": "insufficient_data",
            "candle_count": len(candles),
        }
        job.status = JobStatus.Complete.value
        return

    highs = pd.Series([float(c.high) for c in candles])
    lows = pd.Series([float(c.low) for c in candles])
    closes = pd.Series([float(c.close) for c in candles])
    volumes = pd.Series([float(c.volume) for c in candles])

    snap = compute_all(closes, volumes)
    if snap is None:
        job.response = {"status": "compute_failed", "candle_count": len(candles)}
        job.status = JobStatus.Complete.value
        return

    # S/R 분석
    sr = analyze_sr(highs, lows, closes)

    # 패턴 분석: 대상 마켓에서만 (BTC, ETH 등)
    pat_markets = [
        m.strip() for m in settings.pattern_markets.split(",") if m.strip()
    ]
    use_patterns = not pat_markets or market in pat_markets
    pat = (
        analyze_patterns(
            highs, lows, closes,
            timeframe=timeframe, volumes=volumes,
        )
        if use_patterns
        else None
    )

    # 지표 데이터 조립
    indicator_data: dict[str, Any] = {
        **snap.to_dict(),
        "support_resistance": sr.to_dict(),
        "patterns": pat.to_dict() if pat else {"patterns": [], "conflicts": []},
        "close_price": float(candles[-1].close),
        "candle_count": len(candles),
    }

    # indicator_snapshots upsert
    candle_open_at = candles[-1].open_at
    existing = (await session.execute(
        select(IndicatorSnapshot).where(
            IndicatorSnapshot.market == market,
            IndicatorSnapshot.timeframe == timeframe,
            IndicatorSnapshot.candle_open_at == candle_open_at,
        )
    )).scalar_one_or_none()

    if existing:
        existing.indicators = indicator_data
    else:
        session.add(IndicatorSnapshot(
            market=market,
            timeframe=timeframe,
            candle_open_at=candle_open_at,
            indicators=indicator_data,
        ))

    logger.info(
        "compute_indicators: %s %s saved (candles=%d)",
        market, timeframe, len(candles),
    )

    # 분석 TF → strategy_queue에 이벤트 push
    queued = False
    if not ref_only and _strategy_queue is not None:
        from core.strategy.strategy_scheduler import StrategyEvent

        await _strategy_queue.put(StrategyEvent(market=market, timeframe=timeframe))
        queued = True

    job.response = {
        "status": "ok",
        "candle_count": len(candles),
        "timeframe": timeframe,
        "strategy_queued": queued,
    }
    job.status = JobStatus.Complete.value
