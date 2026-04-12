"""일봉 수집 유틸 — 백필, 일봉 수집, 패턴 캐시 동기화, Job 정리.

스케줄 루프는 unified_scheduler.py에서 관리.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.candle import Candle
from models.job import Job
from models.pattern_config import PatternConfig
from models.tracked_asset import TrackedAsset
from utils.config import Settings

# Job 보관 기간
JOB_RETAIN_COMPLETE_DAYS = 3
JOB_RETAIN_ERROR_DAYS = 7

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
TIMEFRAME = "1d"
BACKFILL_DAYS = 200


def _fetch_daily_candles_sync(
    settings: Settings, market: str, count: int, to: str | None = None,
) -> list[dict[str, Any]]:
    """빗썸 일봉 API 동기 호출."""
    from job_runner.workers import PublicRequester

    params: dict[str, Any] = {"market": market, "count": count}
    if to is not None:
        params["to"] = to
    with PublicRequester(settings) as pub:
        data = pub.request("GET", "/v1/candles/days", params=params)
    if not isinstance(data, list):
        msg = "candles/days 응답이 리스트가 아닙니다."
        raise TypeError(msg)
    return data


async def _upsert_daily_candles(
    session: AsyncSession, raw_candles: list[dict[str, Any]],
) -> int:
    """일봉 데이터를 candles 테이블에 upsert."""
    count = 0
    for c in raw_candles:
        market = c["market"]
        open_at = datetime.fromisoformat(
            c["candle_date_time_utc"]
        ).replace(tzinfo=UTC)

        existing = (await session.execute(
            select(Candle).where(
                Candle.market == market,
                Candle.timeframe == TIMEFRAME,
                Candle.open_at == open_at,
            )
        )).scalar_one_or_none()

        if existing is not None:
            existing.open = Decimal(str(c["opening_price"]))
            existing.high = Decimal(str(c["high_price"]))
            existing.low = Decimal(str(c["low_price"]))
            existing.close = Decimal(str(c["trade_price"]))
            existing.volume = Decimal(str(c["candle_acc_trade_volume"]))
        else:
            session.add(Candle(
                market=market,
                timeframe=TIMEFRAME,
                open_at=open_at,
                open=Decimal(str(c["opening_price"])),
                high=Decimal(str(c["high_price"])),
                low=Decimal(str(c["low_price"])),
                close=Decimal(str(c["trade_price"])),
                volume=Decimal(str(c["candle_acc_trade_volume"])),
            ))
        count += 1
    return count


async def _active_markets(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(TrackedAsset.market)
        .where(TrackedAsset.is_active.is_(True))
        .distinct()
    )
    return [row[0] for row in result.all()]


async def _backfill_market(
    session: AsyncSession, settings: Settings, market: str,
) -> int:
    """마켓의 일봉 200일치 백필. 이미 충분하면 스킵."""
    existing_count = (await session.execute(
        select(func.count()).select_from(Candle).where(
            Candle.market == market,
            Candle.timeframe == TIMEFRAME,
        )
    )).scalar() or 0

    if existing_count >= BACKFILL_DAYS:
        logger.info("daily backfill skip: %s already has %d candles", market, existing_count)
        return 0

    need = BACKFILL_DAYS + 10  # 여유분
    all_candles: list[dict[str, Any]] = []
    to_param: str | None = None

    while len(all_candles) < need:
        batch_size = min(200, need - len(all_candles))
        raw = await asyncio.to_thread(
            _fetch_daily_candles_sync, settings, market, batch_size, to_param,
        )
        if not raw:
            break
        all_candles.extend(raw)
        to_param = raw[-1]["candle_date_time_utc"]
        await asyncio.sleep(0.15)

    if all_candles:
        upserted = await _upsert_daily_candles(session, all_candles)
        await session.commit()
        logger.info("daily backfill: %s → %d candles upserted", market, upserted)
        return upserted
    return 0


async def _collect_latest_daily(
    session: AsyncSession, settings: Settings, markets: list[str],
) -> int:
    """각 마켓의 최신 일봉 1개 수집."""
    total = 0
    for market in markets:
        raw = await asyncio.to_thread(
            _fetch_daily_candles_sync, settings, market, 2,
        )
        if raw:
            upserted = await _upsert_daily_candles(session, raw)
            total += upserted
        await asyncio.sleep(0.15)
    if total:
        await session.commit()
    return total


async def _sync_pattern_cache(session: AsyncSession) -> None:
    """DB pattern_configs → 메모리 캐시 동기화."""
    from analysis.patterns import set_active_patterns
    from executor.position_sizing import set_pattern_sizing

    rows = (await session.execute(
        select(PatternConfig)
    )).scalars().all()

    if not rows:
        logger.info("pattern_configs empty, using fallback defaults")
        return

    active_names = {r.name for r in rows if r.active}
    allocation = {r.name: r.buy_allocation_pct for r in rows}
    sell_rec = {r.name: r.sell_recommendation_pct for r in rows}

    set_active_patterns(active_names)
    set_pattern_sizing(allocation, sell_rec)
    logger.info("pattern cache synced: %d active / %d total", len(active_names), len(rows))


async def _cleanup_old_jobs(session: AsyncSession) -> dict[str, int]:
    """오래된 Job 정리. Complete 3일, Error/Running(stuck) 7일."""
    now = datetime.now(UTC)
    counts: dict[str, int] = {}

    # Complete: 3일 이전
    result = await session.execute(
        delete(Job)
        .where(Job.status == "Complete", Job.updated_at < now - timedelta(days=JOB_RETAIN_COMPLETE_DAYS))
    )
    counts["complete"] = result.rowcount  # type: ignore[assignment]

    # Error: 7일 이전
    result = await session.execute(
        delete(Job)
        .where(Job.status == "Error", Job.updated_at < now - timedelta(days=JOB_RETAIN_ERROR_DAYS))
    )
    counts["error"] = result.rowcount  # type: ignore[assignment]

    # Stuck Running: 1일 이전
    result = await session.execute(
        delete(Job)
        .where(Job.status == "Running", Job.updated_at < now - timedelta(days=1))
    )
    counts["stuck"] = result.rowcount  # type: ignore[assignment]

    total = sum(counts.values())
    if total > 0:
        await session.commit()
        logger.info("job cleanup: %s deleted (complete=%d, error=%d, stuck=%d)",
                     total, counts["complete"], counts["error"], counts["stuck"])
    return counts


