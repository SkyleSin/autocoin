"""패턴 설정 DB 관리 — 활성 목록, 포지션 사이징, 적중률 갱신."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.candle import Candle
from models.pattern_config import PatternConfig
from models.pattern_evaluation import PatternEvaluation
from models.signal import Signal

logger = logging.getLogger(__name__)

# ── 캐시 (서버 수명 동안 유지, 일봉 스케줄러가 갱신) ──
_cache: dict[str, PatternConfig] | None = None
_cache_ts: float = 0
_CACHE_TTL = 300  # 5분


async def get_all_configs(session: AsyncSession) -> dict[str, PatternConfig]:
    """전체 패턴 설정 조회 (캐시 사용)."""
    global _cache, _cache_ts
    now = datetime.now(UTC).timestamp()
    if _cache is not None and now - _cache_ts < _CACHE_TTL:
        return _cache
    rows = (await session.execute(
        select(PatternConfig)
    )).scalars().all()
    _cache = {r.name: r for r in rows}
    _cache_ts = now
    return _cache


def invalidate_cache() -> None:
    """캐시 무효화 (설정 변경 시 호출)."""
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0


async def get_active_pattern_names(session: AsyncSession) -> set[str]:
    """활성 패턴 이름 set 반환."""
    configs = await get_all_configs(session)
    return {name for name, cfg in configs.items() if cfg.active}


async def get_pattern_ko(session: AsyncSession) -> dict[str, str]:
    """영문→한글 매핑."""
    configs = await get_all_configs(session)
    return {name: cfg.name_ko for name, cfg in configs.items()}


async def get_buy_allocation(session: AsyncSession, pattern_name: str) -> float:
    """매수 시 기본 비율 (%)."""
    configs = await get_all_configs(session)
    cfg = configs.get(pattern_name)
    return cfg.buy_allocation_pct if cfg else 10.0


async def get_sell_recommendation(session: AsyncSession, pattern_name: str) -> int:
    """매도 시 추천 비율 (%)."""
    configs = await get_all_configs(session)
    cfg = configs.get(pattern_name)
    return cfg.sell_recommendation_pct if cfg else 40


async def evaluate_pending_signals(
    session: AsyncSession,
    pattern_markets: list[str] | None = None,
) -> dict[str, int]:
    """7일 전 미판정 시그널을 일괄 판정하고 pattern_evaluations에 기록.

    Args:
        pattern_markets: 패턴 적중률 추적 대상 마켓 (None이면 전체)

    Returns:
        {"evaluated": N, "hit": N}
    """
    configs = await get_all_configs(session)
    now = datetime.now(UTC)
    result = {"evaluated": 0, "hit": 0}

    # 모든 패턴의 eval_days 값 수집 (보통 7)
    eval_days_set = {cfg.eval_days for cfg in configs.values()}

    for eval_days in eval_days_set:
        # eval_days일 전 ~ eval_days+1일 전 사이의 미검증 시그널
        target_start = now - timedelta(days=eval_days + 1)
        target_end = now - timedelta(days=eval_days)

        conditions = [
            Signal.source == "technical",
            Signal.signal_type.in_(["BUY", "SELL"]),
            Signal.verified.is_(False),
            Signal.created_at >= target_start,
            Signal.created_at < target_end,
        ]
        if pattern_markets:
            conditions.append(Signal.market.in_(pattern_markets))

        signals = (await session.execute(
            select(Signal).where(*conditions)
        )).scalars().all()

        for sig in signals:
            # 시그널 시점 일봉 종가
            signal_candle = (await session.execute(
                select(Candle).where(
                    Candle.market == sig.market,
                    Candle.timeframe == "1d",
                    Candle.open_at <= sig.created_at,
                ).order_by(Candle.open_at.desc()).limit(1)
            )).scalar_one_or_none()

            # 판정일 일봉 종가
            eval_target = sig.created_at + timedelta(days=eval_days)
            eval_candle = (await session.execute(
                select(Candle).where(
                    Candle.market == sig.market,
                    Candle.timeframe == "1d",
                    Candle.open_at >= eval_target,
                ).order_by(Candle.open_at.asc()).limit(1)
            )).scalar_one_or_none()

            if not signal_candle or not eval_candle:
                continue

            signal_price = float(signal_candle.close)
            eval_price = float(eval_candle.close)
            if signal_price <= 0:
                continue

            pct_change = (eval_price - signal_price) / signal_price * 100
            hit = pct_change > 0 if sig.signal_type == "BUY" else pct_change < 0

            # 패턴명 추출
            pattern_name = ""
            if sig.indicators and isinstance(sig.indicators.get("patterns"), dict):
                pats = sig.indicators["patterns"].get("patterns", [])
                if pats:
                    pattern_name = pats[0].get("name", "")

            if not pattern_name:
                # 패턴 없는 시그널은 판정만 하고 스킵
                sig.verified = True
                continue

            # pattern_evaluations에 기록
            session.add(PatternEvaluation(
                pattern_name=pattern_name,
                signal_type=sig.signal_type,
                market=sig.market,
                signal_date=sig.created_at,
                signal_price=signal_price,
                eval_date=eval_candle.open_at,
                eval_price=eval_price,
                pct_change=round(pct_change, 3),
                hit=hit,
                signal_id=sig.id,
            ))

            sig.verified = True
            result["evaluated"] += 1
            if hit:
                result["hit"] += 1

    if result["evaluated"] > 0:
        await session.commit()
        # 적중률 재계산
        await _recalculate_accuracy(session)

    return result


async def _recalculate_accuracy(session: AsyncSession) -> None:
    """pattern_evaluations 기반으로 pattern_configs.accuracy_pct 재계산."""
    stats = (await session.execute(
        select(
            PatternEvaluation.pattern_name,
            func.count().label("total"),
            func.count().filter(PatternEvaluation.hit.is_(True)).label("hits"),
        ).group_by(PatternEvaluation.pattern_name)
    )).all()

    for pattern_name, total, hits in stats:
        cfg = (await session.execute(
            select(PatternConfig).where(PatternConfig.name == pattern_name)
        )).scalar_one_or_none()
        if cfg:
            cfg.total_count = total
            cfg.hit_count = hits
            cfg.accuracy_pct = round(hits / total * 100, 1) if total > 0 else 0.0

    await session.commit()
    invalidate_cache()
    logger.info("pattern accuracy recalculated: %d patterns", len(stats))
