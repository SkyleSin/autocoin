"""캔들 수집 핸들러 — collect_candles."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_runner.exceptions import JobExecutionFailed
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.workers import PublicRequester
from models.candle import Candle
from models.job import Job
from models.job_status import JobStatus
from utils.config import Settings

_UNIT_TO_TIMEFRAME = {
    1: "1m", 3: "3m", 5: "5m", 10: "10m",
    15: "15m", 30: "30m", 60: "1h", 240: "4h",
}


def _candles_minutes_sync(
    settings: Settings, unit: int, market: str, count: int, to: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"market": market, "count": count}
    if to is not None:
        params["to"] = to
    with PublicRequester(settings) as pub:
        data = pub.request("GET", f"/v1/candles/minutes/{unit}", params=params)
    if not isinstance(data, list):
        raise JobExecutionFailed(f"candles/minutes/{unit} 응답이 리스트가 아닙니다.")
    return data


async def _upsert_candles(
    session: AsyncSession, raw_candles: list[dict[str, Any]], timeframe: str,
) -> int:
    """빗썸 캔들 응답을 Candle 테이블에 upsert. 삽입/갱신된 행 수 반환."""
    count = 0
    for c in raw_candles:
        market = c["market"]
        open_at_str = c["candle_date_time_utc"]
        open_at = datetime.fromisoformat(open_at_str).replace(tzinfo=UTC)

        existing = (
            await session.execute(
                select(Candle).where(
                    Candle.market == market,
                    Candle.timeframe == timeframe,
                    Candle.open_at == open_at,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.open = Decimal(str(c["opening_price"]))
            existing.high = Decimal(str(c["high_price"]))
            existing.low = Decimal(str(c["low_price"]))
            existing.close = Decimal(str(c["trade_price"]))
            existing.volume = Decimal(str(c["candle_acc_trade_volume"]))
        else:
            session.add(Candle(
                market=market,
                timeframe=timeframe,
                open_at=open_at,
                open=Decimal(str(c["opening_price"])),
                high=Decimal(str(c["high_price"])),
                low=Decimal(str(c["low_price"])),
                close=Decimal(str(c["trade_price"])),
                volume=Decimal(str(c["candle_acc_trade_volume"])),
            ))
        count += 1
    return count


async def handle_collect_candles(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession | None,
) -> None:
    if not job.content or not str(job.content.get("market", "")).strip():
        raise ValueError("collect_candles 잡에 content.market 이 필요합니다.")
    market = str(job.content["market"]).strip()
    unit = int(job.content.get("unit", 1))
    count = int(job.content.get("count", 1))
    to = job.content.get("to")
    timeframe = _UNIT_TO_TIMEFRAME.get(unit, f"{unit}m")

    if rate_limiter is not None:
        await rate_limiter.acquire()
    raw = await asyncio.to_thread(
        _candles_minutes_sync, settings, unit, market, count, to,
    )

    if session is not None:
        upserted = await _upsert_candles(session, raw, timeframe)
        job.response = {"upserted": upserted, "timeframe": timeframe}
        ref_only = bool(job.content.get("ref_only", False))
        # 수집 완료 → analyze_technical Job 자동 생성 (참조 TF는 제외)
        if not ref_only:
            analyze_job = Job(
                type="analyze_technical",
                status=JobStatus.Ready.value,
                content={"market": market, "timeframe": timeframe},
                priority=20,
            )
            session.add(analyze_job)
    else:
        job.response = {"candles": raw, "timeframe": timeframe}
    job.status = JobStatus.Complete.value
