"""캔들 서비스 — 마켓 조회, 분봉/일봉 Job 생성, 일봉 백필."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.candle import Candle
from models.job import Job
from models.job_status import JobStatus
from models.tracked_asset import TrackedAsset
from utils.config import Settings

logger = logging.getLogger(__name__)

DAILY_TIMEFRAME = "1d"
BACKFILL_DAYS = 200

# 기본 수집 마켓 — TrackedAsset에 없어도 항상 수집
_BASE_MARKETS = ["KRW-BTC", "KRW-ETH"]

_LABEL_TO_UNIT = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10,
    "15m": 15, "30m": 30, "1h": 60, "4h": 240,
}


# ---------------------------------------------------------------------------
# 공용
# ---------------------------------------------------------------------------

async def active_markets(session: AsyncSession) -> list[str]:
    """활성 TrackedAsset + 기본 마켓(BTC/ETH) 목록(중복 제거)."""
    result = await session.execute(
        select(TrackedAsset.market)
        .where(TrackedAsset.is_active.is_(True))
        .distinct()
    )
    markets = [row[0] for row in result.all()]
    for m in _BASE_MARKETS:
        if m not in markets:
            markets.append(m)
    return markets


# ---------------------------------------------------------------------------
# 분봉 수집
# ---------------------------------------------------------------------------

async def create_candle_jobs(
    session: AsyncSession,
    markets: list[str],
    unit: int,
    count: int,
    ref_only: bool = False,
) -> int:
    """각 마켓에 대해 collect_candles Job 생성. 생성 개수 반환."""
    created = 0
    for market in markets:
        job = Job(
            type="collect_candles",
            status=JobStatus.Ready.value,
            content={
                "market": market, "unit": unit,
                "count": count, "ref_only": ref_only,
            },
            priority=10,
        )
        session.add(job)
        created += 1
    if created:
        await session.commit()
    return created


# ---------------------------------------------------------------------------
# 일봉 수집
# ---------------------------------------------------------------------------

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
                Candle.timeframe == DAILY_TIMEFRAME,
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
                timeframe=DAILY_TIMEFRAME,
                open_at=open_at,
                open=Decimal(str(c["opening_price"])),
                high=Decimal(str(c["high_price"])),
                low=Decimal(str(c["low_price"])),
                close=Decimal(str(c["trade_price"])),
                volume=Decimal(str(c["candle_acc_trade_volume"])),
            ))
        count += 1
    return count


async def collect_latest_daily(
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


async def backfill_market(
    session: AsyncSession, settings: Settings, market: str,
) -> int:
    """마켓의 일봉 + 분봉(15m/1h/4h) 백필. 이미 충분하면 스킵."""
    total_upserted = 0

    # ── 일봉 백필 ──
    total_upserted += await _backfill_daily(session, settings, market)

    # ── 분봉 백필 (15m, 1h, 4h) ──
    ref_tf_str = settings.candle_ref_timeframes  # "1h,4h"
    main_unit = settings.candle_unit             # 15
    main_label = next(
        (k for k, v in _LABEL_TO_UNIT.items() if v == main_unit), None,
    )

    tf_labels = [main_label] if main_label else []
    for tf in ref_tf_str.split(","):
        tf = tf.strip()
        if tf and tf not in tf_labels:
            tf_labels.append(tf)

    for tf_label in tf_labels:
        unit = _LABEL_TO_UNIT.get(tf_label)
        if not unit:
            continue
        total_upserted += await _backfill_minutes(
            session, settings, market, unit, tf_label,
        )

    return total_upserted


async def _backfill_daily(
    session: AsyncSession, settings: Settings, market: str,
) -> int:
    """일봉 200일치 백필."""
    existing_count = (await session.execute(
        select(func.count()).select_from(Candle).where(
            Candle.market == market,
            Candle.timeframe == DAILY_TIMEFRAME,
        )
    )).scalar() or 0

    if existing_count >= BACKFILL_DAYS:
        return 0

    need = BACKFILL_DAYS + 10
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
        logger.info("daily backfill: %s → %d candles", market, upserted)
        return upserted
    return 0


def _fetch_minutes_sync(
    settings: Settings, market: str, unit: int, count: int,
    to: str | None = None,
) -> list[dict[str, Any]]:
    """빗썸 분봉 API 동기 호출."""
    from job_runner.workers import PublicRequester

    params: dict[str, Any] = {"market": market, "count": count}
    if to is not None:
        params["to"] = to
    with PublicRequester(settings) as pub:
        data = pub.request("GET", f"/v1/candles/minutes/{unit}", params=params)
    if not isinstance(data, list):
        return []
    return data


async def _backfill_minutes(
    session: AsyncSession, settings: Settings, market: str,
    unit: int, tf_label: str,
) -> int:
    """분봉 200개 백필 (15m/1h/4h)."""
    existing_count = (await session.execute(
        select(func.count()).select_from(Candle).where(
            Candle.market == market,
            Candle.timeframe == tf_label,
        )
    )).scalar() or 0

    if existing_count >= 200:
        return 0

    raw = await asyncio.to_thread(
        _fetch_minutes_sync, settings, market, unit, 200,
    )
    if not raw:
        return 0

    count = 0
    for c in raw:
        open_at = datetime.fromisoformat(
            c["candle_date_time_utc"]
        ).replace(tzinfo=UTC)

        existing = (await session.execute(
            select(Candle).where(
                Candle.market == market,
                Candle.timeframe == tf_label,
                Candle.open_at == open_at,
            )
        )).scalar_one_or_none()

        if existing is None:
            session.add(Candle(
                market=market,
                timeframe=tf_label,
                open_at=open_at,
                open=Decimal(str(c["opening_price"])),
                high=Decimal(str(c["high_price"])),
                low=Decimal(str(c["low_price"])),
                close=Decimal(str(c["trade_price"])),
                volume=Decimal(str(c["candle_acc_trade_volume"])),
            ))
            count += 1

    if count:
        await session.commit()
        logger.info("backfill %s: %s %s → %d candles", tf_label, market, tf_label, count)
    return count
