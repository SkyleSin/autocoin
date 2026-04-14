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
from models.signal import Signal
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


async def _update_indicator_weights(session: AsyncSession) -> dict[str, int]:
    """최근 시그널 적중률을 집계하여 indicator_weights 갱신.

    - 5분봉: 2시간 후 가격 비교
    - 15분봉: 8시간 후 가격 비교
    - 1시간봉: 24시간 후 가격 비교
    최소 30건 이상일 때만 갱신. EMA 방식 (0.8 × 현재 + 0.2 × 최근).
    """
    from analysis.signal_weights import Weight, set_weights_cache
    from models.indicator_weight import IndicatorWeight

    eval_intervals = {"5m": "2 hours", "15m": "8 hours", "1h": "24 hours"}
    min_samples = 30
    ema_alpha = 0.2
    result_counts = {"updated": 0, "skipped": 0}

    for tf, interval in eval_intervals.items():
        # 최근 시그널에서 지표별 적중률 집계
        # technical 시그널의 indicators JSON에서 fired 지표를 추출
        signals = (await session.execute(
            select(Signal).where(
                Signal.source == "technical",
                Signal.signal_type.in_(["BUY", "SELL"]),
                Signal.timeframe == tf,
                Signal.created_at >= datetime.now(UTC) - timedelta(days=30),
            ).order_by(Signal.created_at)
        )).scalars().all()

        if len(signals) < min_samples:
            result_counts["skipped"] += 1
            continue

        # 각 시그널에 대해 미래 가격 확인
        ind_stats: dict[str, dict] = {}
        for sig in signals:
            future_candle = (await session.execute(
                select(Candle).where(
                    Candle.market == sig.market,
                    Candle.timeframe == tf,
                    Candle.open_at >= sig.created_at + timedelta(
                        hours=int(interval.split()[0]),
                    ),
                ).order_by(Candle.open_at).limit(1)
            )).scalar_one_or_none()

            if not future_candle:
                continue

            # 시그널 시점 가격
            signal_candle = (await session.execute(
                select(Candle).where(
                    Candle.market == sig.market,
                    Candle.timeframe == tf,
                    Candle.open_at <= sig.created_at,
                ).order_by(Candle.open_at.desc()).limit(1)
            )).scalar_one_or_none()

            if not signal_candle or float(signal_candle.close) == 0:
                continue

            entry = float(signal_candle.close)
            future = float(future_candle.close)
            hit = (future > entry) if sig.signal_type == "BUY" else (future < entry)

            # indicators JSON에서 fired 지표 추출
            inds = sig.indicators or {}
            fired_indicators = _extract_fired_indicators(inds, sig.signal_type)

            for ind_name in fired_indicators:
                if ind_name not in ind_stats:
                    ind_stats[ind_name] = {"total": 0, "hits": 0, "direction": sig.signal_type}
                ind_stats[ind_name]["total"] += 1
                if hit:
                    ind_stats[ind_name]["hits"] += 1

        # DB 업데이트
        tf_weights: dict[str, Weight] = {}
        for ind_name, stats in ind_stats.items():
            if stats["total"] < min_samples:
                continue

            new_accuracy = stats["hits"] / stats["total"]
            direction = stats["direction"]

            existing = (await session.execute(
                select(IndicatorWeight).where(
                    IndicatorWeight.name == ind_name,
                    IndicatorWeight.direction == direction,
                    IndicatorWeight.timeframe == tf,
                )
            )).scalar_one_or_none()

            if existing:
                # EMA 갱신
                updated_acc = (1 - ema_alpha) * existing.accuracy + ema_alpha * new_accuracy
                existing.accuracy = round(updated_acc, 4)
                existing.sample_count = stats["total"]
                existing.hit_count = stats["hits"]
                existing.is_active = updated_acc >= 0.50
            else:
                session.add(IndicatorWeight(
                    name=ind_name,
                    direction=direction,
                    timeframe=tf,
                    accuracy=round(new_accuracy, 4),
                    sample_count=stats["total"],
                    hit_count=stats["hits"],
                    is_active=new_accuracy >= 0.50,
                ))

            tf_weights[ind_name] = Weight(
                ind_name, direction,
                existing.accuracy if existing else round(new_accuracy, 4),
                (existing.accuracy if existing else new_accuracy) >= 0.50,
            )
            result_counts["updated"] += 1

        if tf_weights:
            set_weights_cache(tf, tf_weights)

    if result_counts["updated"] > 0:
        await session.commit()
        logger.info(
            "indicator weights updated: %d indicators, %d skipped",
            result_counts["updated"], result_counts["skipped"],
        )
    return result_counts


def _extract_fired_indicators(indicators: dict, signal_type: str) -> list[str]:
    """시그널의 indicators JSON에서 활성화된 지표명을 추출."""
    fired = []

    rsi = indicators.get("rsi_14")
    if rsi is not None:
        if rsi > 70:
            fired.append("rsi_overbought")
        elif rsi < 30:
            fired.append("rsi_oversold")

    bb_upper = indicators.get("bb_upper")
    bb_lower = indicators.get("bb_lower")
    close = indicators.get("close")
    if close and bb_upper and float(close) >= float(bb_upper):
        fired.append("bb_upper")
    if close and bb_lower and float(close) <= float(bb_lower):
        fired.append("bb_lower")

    macd_h = indicators.get("macd_histogram")
    macd_h_prev = indicators.get("macd_histogram_prev")
    if macd_h is not None and macd_h_prev is not None:
        if float(macd_h) < 0 and float(macd_h_prev) >= 0:
            fired.append("macd_dead")
        elif float(macd_h) > 0 and float(macd_h_prev) <= 0:
            fired.append("macd_golden")

    sr = indicators.get("support_resistance", {})
    sr_signal = sr.get("signal", "NONE") if isinstance(sr, dict) else "NONE"
    if sr_signal != "NONE":
        fired.append(f"sr_{sr_signal.lower()}")

    return fired


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


