"""MFE/MAE 트래커 — 시그널 경로 품질 추적.

MFE (Maximum Favorable Excursion): 진입 후 최대 유리 가격
MAE (Maximum Adverse Excursion): 진입 후 최대 불리 가격

케이스 A: 100→101→103→107→110  MFE=110, MAE=100 → 깔끔한 진입
케이스 B: 100→85→78→90→110     MFE=110, MAE=78  → 지옥 구간 버팀

품질 점수:
  quality = (수익률 × 0.4) + (MFE 활용도 × 0.3) + (MAE 안정성 × 0.3)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.signal import Signal

logger = logging.getLogger(__name__)

# 품질 점수 가중치
_W_PROFIT = 0.4
_W_MFE_EFF = 0.3
_W_MAE_SAFE = 0.3

# MAE 안정성 계산 기준 (이 이상 MAE면 안정성 0)
_MAE_THRESHOLD = 0.10  # 10%


async def update_mfe_mae(
    session: AsyncSession,
    market: str,
    candle_high: float,
    candle_low: float,
) -> int:
    """미종료 BUY/SELL 시그널의 MFE/MAE를 캔들 고가/저가로 갱신.

    캔들 수집 시점마다 호출. 해당 마켓의 열린 시그널만 대상.

    Returns:
        갱신된 시그널 수
    """
    open_signals = (await session.execute(
        select(Signal).where(
            Signal.market == market,
            Signal.signal_type.in_(["BUY", "SELL"]),
            Signal.entry_price.isnot(None),
            Signal.closed_at.is_(None),
        )
    )).scalars().all()

    updated = 0
    for sig in open_signals:
        changed = False

        if sig.signal_type == "BUY":
            # BUY: 고가가 MFE, 저가가 MAE
            if sig.mfe is None or candle_high > sig.mfe:
                sig.mfe = candle_high
                changed = True
            if sig.mae is None or candle_low < sig.mae:
                sig.mae = candle_low
                changed = True
        elif sig.signal_type == "SELL":
            # SELL: 저가가 MFE (가격 하락이 유리), 고가가 MAE
            if sig.mfe is None or candle_low < sig.mfe:
                sig.mfe = candle_low
                changed = True
            if sig.mae is None or candle_high > sig.mae:
                sig.mae = candle_high
                changed = True

        if changed:
            updated += 1

    if updated:
        logger.debug("mfe_mae: %s updated %d signals", market, updated)
    return updated


async def close_signal(
    session: AsyncSession,
    signal: Signal,
    exit_price: float,
) -> float | None:
    """시그널을 종료하고 품질 점수를 계산.

    Returns:
        quality_score (0.0~1.0) 또는 None (계산 불가)
    """
    signal.exit_price = exit_price
    signal.closed_at = datetime.now(UTC)
    signal.quality_score = calculate_quality(signal)
    signal.verified = True
    return signal.quality_score


async def close_opposite_signals(
    session: AsyncSession,
    market: str,
    new_signal_type: str,
    exit_price: float,
) -> int:
    """새 시그널과 반대 방향의 미종료 시그널을 일괄 종료.

    BUY 시그널 → 기존 SELL 종료, SELL 시그널 → 기존 BUY 종료.

    Returns:
        종료된 시그널 수
    """
    opposite = "BUY" if new_signal_type == "SELL" else "SELL"
    open_signals = (await session.execute(
        select(Signal).where(
            Signal.market == market,
            Signal.signal_type == opposite,
            Signal.entry_price.isnot(None),
            Signal.closed_at.is_(None),
        )
    )).scalars().all()

    closed = 0
    for sig in open_signals:
        await close_signal(session, sig, exit_price)
        closed += 1
        logger.info(
            "mfe_mae: closed %s signal %s (entry=%.0f exit=%.0f "
            "mfe=%.0f mae=%.0f quality=%.3f)",
            sig.signal_type, sig.id,
            sig.entry_price or 0, exit_price,
            sig.mfe or 0, sig.mae or 0,
            sig.quality_score or 0,
        )

    return closed


def calculate_quality(signal: Signal) -> float | None:
    """MFE/MAE 기반 품질 점수 계산.

    quality = (수익률 점수 × 0.4) + (MFE 활용도 × 0.3) + (MAE 안정성 × 0.3)

    - 수익률 점수: 수익이면 높음, 손실이면 낮음 (0~1 클램프)
    - MFE 활용도: 실현수익/최대수익 (최고점에서 얼마나 잘 청산했는가)
    - MAE 안정성: MAE가 작을수록 좋음 (진입 타이밍이 좋았는가)

    Returns:
        0.0~1.0 또는 None
    """
    entry = signal.entry_price
    exit_ = signal.exit_price
    mfe = signal.mfe
    mae = signal.mae

    if not all([entry, exit_]) or entry == 0:
        return None

    # MFE/MAE 없으면 entry로 대체
    if mfe is None:
        mfe = exit_
    if mae is None:
        mae = entry

    if signal.signal_type == "BUY":
        profit_pct = (exit_ - entry) / entry
        mfe_pct = (mfe - entry) / entry
        mae_pct = (entry - mae) / entry  # 양수 = 하락폭
    else:  # SELL
        profit_pct = (entry - exit_) / entry
        mfe_pct = (entry - mfe) / entry
        mae_pct = (mae - entry) / entry  # 양수 = 상승폭

    # 1) 수익률 점수: -5%→0, 0%→0.33, +10%→1.0
    profit_score = max(0.0, min(1.0, (profit_pct + 0.05) / 0.15))

    # 2) MFE 활용도: 실현수익/최대수익
    if mfe_pct > 0:
        mfe_efficiency = max(0.0, min(1.0, profit_pct / mfe_pct))
    else:
        mfe_efficiency = 1.0 if profit_pct >= 0 else 0.0

    # 3) MAE 안정성: MAE가 작을수록 좋음
    mae_safety = max(0.0, 1.0 - mae_pct / _MAE_THRESHOLD)

    quality = (
        profit_score * _W_PROFIT
        + mfe_efficiency * _W_MFE_EFF
        + mae_safety * _W_MAE_SAFE
    )
    return round(max(0.0, min(1.0, quality)), 4)
