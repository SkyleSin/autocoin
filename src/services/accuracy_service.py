"""패턴 적중률 계산 — Signal 발생 후 4시간 뒤 가격과 비교."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.candle import Candle
from models.pattern_accuracy import PatternAccuracy
from models.signal import Signal

logger = logging.getLogger(__name__)

# 적중 판정까지 대기 시간 (4시간 = 15분봉 16개)
VERIFY_AFTER = timedelta(hours=4)


async def verify_pending_signals(session: AsyncSession) -> dict[str, int]:
    """미검증 시그널을 일괄 검증 → pattern_accuracy 갱신.

    Returns:
        {"verified": N, "hit": M} 처리 건수
    """
    cutoff = datetime.now(UTC) - VERIFY_AFTER

    # 1) LLM 시그널: 활성 패턴 적중률 (BUY/SELL만)
    rows = (await session.execute(
        select(Signal).where(
            Signal.verified.is_(False),
            Signal.signal_type.in_(["BUY", "SELL"]),
            Signal.created_at <= cutoff,
            Signal.source.in_(["local_llm", "deepseek"]),
        ).order_by(Signal.created_at.asc())
        .limit(200)
    )).scalars().all()

    # 2) Technical 시그널: 비활성 패턴 관찰용 (HOLD 포함)
    tech_rows = (await session.execute(
        select(Signal).where(
            Signal.verified.is_(False),
            Signal.created_at <= cutoff,
            Signal.source == "technical",
        ).order_by(Signal.created_at.asc())
        .limit(200)
    )).scalars().all()

    if not rows:
        return {"verified": 0, "hit": 0}

    stats: dict[str, dict[str, int]] = {}  # pattern -> {total, hit}
    verified_count = 0
    hit_count = 0

    for signal in rows:
        patterns = _extract_patterns(signal.indicators)
        if not patterns:
            signal.verified = True
            continue

        # 시그널 시점 캔들 (기준 가격)
        signal_candle = (await session.execute(
            select(Candle).where(
                Candle.market == signal.market,
                Candle.timeframe == signal.timeframe,
                Candle.open_at <= signal.created_at,
            ).order_by(Candle.open_at.desc()).limit(1)
        )).scalar_one_or_none()

        if signal_candle is None:
            signal.verified = True
            continue

        signal_price = float(signal_candle.close)
        if signal_price <= 0:
            signal.verified = True
            continue

        # 시그널 시점 + 4시간 뒤 캔들 종가 조회
        after_time = signal.created_at + VERIFY_AFTER
        after_candle = (await session.execute(
            select(Candle).where(
                Candle.market == signal.market,
                Candle.timeframe == signal.timeframe,
                Candle.open_at >= after_time,
            ).order_by(Candle.open_at.asc()).limit(1)
        )).scalar_one_or_none()

        if after_candle is None:
            continue  # 아직 캔들 없음

        after_price = float(after_candle.close)
        hit = _check_hit(signal.signal_type, signal_price, after_price)

        signal.verified = True
        verified_count += 1
        if hit:
            hit_count += 1

        # 패턴별 집계
        for pat_name in patterns:
            if pat_name not in stats:
                stats[pat_name] = {"total": 0, "hit": 0}
            stats[pat_name]["total"] += 1
            if hit:
                stats[pat_name]["hit"] += 1

    # 비활성 패턴 검증 (technical 시그널에서 active=False 패턴 추출)
    for signal in tech_rows:
        inactive = _extract_inactive_patterns(signal.indicators)
        if not inactive:
            signal.verified = True
            continue

        signal_candle = (await session.execute(
            select(Candle).where(
                Candle.market == signal.market,
                Candle.timeframe == signal.timeframe,
                Candle.open_at <= signal.created_at,
            ).order_by(Candle.open_at.desc()).limit(1)
        )).scalar_one_or_none()

        if signal_candle is None:
            signal.verified = True
            continue

        signal_price = float(signal_candle.close)
        if signal_price <= 0:
            signal.verified = True
            continue

        after_time = signal.created_at + VERIFY_AFTER
        after_candle = (await session.execute(
            select(Candle).where(
                Candle.market == signal.market,
                Candle.timeframe == signal.timeframe,
                Candle.open_at >= after_time,
            ).order_by(Candle.open_at.asc()).limit(1)
        )).scalar_one_or_none()

        if after_candle is None:
            continue

        signal.verified = True
        after_price = float(after_candle.close)

        for pat_name, pat_signal in inactive:
            hit = _check_hit(pat_signal, signal_price, after_price)
            if pat_name not in stats:
                stats[pat_name] = {"total": 0, "hit": 0}
            stats[pat_name]["total"] += 1
            if hit:
                stats[pat_name]["hit"] += 1
            verified_count += 1
            if hit:
                hit_count += 1

    # pattern_accuracy 테이블 갱신
    now = datetime.now(UTC)
    for pat_name, s in stats.items():
        existing = (await session.execute(
            select(PatternAccuracy).where(
                PatternAccuracy.pattern_name == pat_name,
            )
        )).scalar_one_or_none()

        if existing:
            existing.total_count += s["total"]
            existing.hit_count += s["hit"]
            existing.accuracy_pct = (
                existing.hit_count / existing.total_count * 100
                if existing.total_count > 0 else 0.0
            )
            existing.last_updated_at = now
        else:
            session.add(PatternAccuracy(
                pattern_name=pat_name,
                total_count=s["total"],
                hit_count=s["hit"],
                accuracy_pct=s["hit"] / s["total"] * 100 if s["total"] > 0 else 0.0,
                last_updated_at=now,
            ))

    logger.info("적중률 검증: %d건 처리, %d건 적중", verified_count, hit_count)
    return {"verified": verified_count, "hit": hit_count}


def _extract_patterns(indicators: dict | None) -> list[str]:
    """indicators JSONB에서 패턴 이름 목록 추출."""
    if not indicators:
        return []
    patterns = indicators.get("patterns")
    if isinstance(patterns, dict):
        patterns = patterns.get("patterns", [])
    if not isinstance(patterns, list):
        return []
    return [p["name"] for p in patterns if isinstance(p, dict) and "name" in p]



def _extract_inactive_patterns(
    indicators: dict | None,
) -> list[tuple[str, str]]:
    """indicators에서 active=False인 패턴의 (name, signal) 추출."""
    if not indicators:
        return []
    patterns = indicators.get("patterns")
    if isinstance(patterns, dict):
        patterns = patterns.get("patterns", [])
    if not isinstance(patterns, list):
        return []
    return [
        (p["name"], p["signal"])
        for p in patterns
        if isinstance(p, dict)
        and "name" in p
        and "signal" in p
        and p["signal"] in ("BUY", "SELL")
        and not p.get("active", True)
    ]


def _check_hit(signal_type: str, signal_price: float, after_price: float) -> bool:
    """적중 여부 판정."""
    if signal_type == "BUY":
        return after_price > signal_price
    elif signal_type == "SELL":
        return after_price < signal_price
    return False


async def get_accuracy_summary(
    session: AsyncSession,
    include_inactive: bool = False,
) -> list[dict]:
    """패턴별 적중률 요약 반환 (pattern_configs 우선, 폴백 pattern_accuracies).

    Args:
        include_inactive: True면 total_count=0인 비활성 패턴도 포함
    """
    from models.pattern_config import PatternConfig

    stmt = select(PatternConfig)
    if not include_inactive:
        stmt = stmt.where(PatternConfig.total_count > 0)
    rows = (await session.execute(
        stmt.order_by(PatternConfig.active.desc(), PatternConfig.total_count.desc())
    )).scalars().all()

    if rows:
        return [
            {
                "pattern": r.name,
                "total": r.total_count,
                "hit": r.hit_count,
                "accuracy": r.accuracy_pct,
                "active": r.active,
            }
            for r in rows
        ]

    # 폴백: 레거시 테이블 (active 정보 없음)
    legacy = (await session.execute(
        select(PatternAccuracy)
        .where(PatternAccuracy.total_count > 0)
        .order_by(PatternAccuracy.total_count.desc())
    )).scalars().all()
    return [
        {
            "pattern": r.pattern_name,
            "total": r.total_count,
            "hit": r.hit_count,
            "accuracy": r.accuracy_pct,
            "active": True,
        }
        for r in legacy
    ]
