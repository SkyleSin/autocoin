"""지표 가중치 적응형 갱신 — MFE/MAE quality_score 기반.

기존: 시그널 N시간 후 가격 비교 (단순 적중/미적중)
변경: 매도 시점에 계산된 quality_score 활용 (경로 품질 반영)

- quality_score가 있는 종료 시그널: 품질 기반 평가
- 폴백: quality_score 없으면 기존 방식 (N시간 후 가격 비교)
- 최소 30건 이상일 때만 갱신. EMA 방식 (0.8 × 현재 + 0.2 × 최근).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.analysis.signal_weights import Weight, set_weights_cache
from models.candle import Candle
from models.indicator_weight import IndicatorWeight
from models.signal import Signal

logger = logging.getLogger(__name__)

# 폴백용 (quality_score 없는 시그널)
EVAL_INTERVALS = {"15m": "8 hours", "1h": "24 hours", "4h": "48 hours"}
MIN_SAMPLES = 30
EMA_ALPHA = 0.2

# quality_score → hit 판정 임계값
_QUALITY_HIT_THRESHOLD = 0.5


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
    close = indicators.get("close_price") or indicators.get("close")
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

    # 탑다운 전략 레이어별 지표 추출
    for layer_key in ("layer_4h", "layer_1h", "layer_15m"):
        layer = indicators.get(layer_key, {})
        layer_inds = layer.get("indicators", {})
        if layer_inds:
            layer_fired = _extract_fired_indicators(layer_inds, signal_type)
            for lf in layer_fired:
                prefixed = f"{layer_key.replace('layer_', '')}_{lf}"
                if prefixed not in fired:
                    fired.append(prefixed)

    return fired


def _evaluate_signal_quality(sig: Signal) -> tuple[bool, float] | None:
    """시그널의 품질 평가. quality_score 우선, 없으면 None.

    Returns:
        (hit, weight) 또는 None (평가 불가)
        - hit: 적중 여부
        - weight: 신뢰도 가중치 (quality_score 기반은 1.0, 폴백은 0.5)
    """
    if sig.quality_score is not None:
        return sig.quality_score >= _QUALITY_HIT_THRESHOLD, 1.0
    return None


async def _fallback_evaluate(
    session: AsyncSession,
    sig: Signal,
    tf: str,
) -> tuple[bool, float] | None:
    """quality_score 없는 시그널: 기존 N시간 후 가격 비교.

    Returns:
        (hit, weight=0.5) 또는 None
    """
    interval = EVAL_INTERVALS.get(tf)
    if not interval:
        return None

    hours = int(interval.split()[0])
    future_candle = (await session.execute(
        select(Candle).where(
            Candle.market == sig.market,
            Candle.timeframe == tf,
            Candle.open_at >= sig.created_at + timedelta(hours=hours),
        ).order_by(Candle.open_at).limit(1)
    )).scalar_one_or_none()

    if not future_candle:
        return None

    signal_candle = (await session.execute(
        select(Candle).where(
            Candle.market == sig.market,
            Candle.timeframe == tf,
            Candle.open_at <= sig.created_at,
        ).order_by(Candle.open_at.desc()).limit(1)
    )).scalar_one_or_none()

    if not signal_candle or float(signal_candle.close) == 0:
        return None

    entry = float(signal_candle.close)
    future = float(future_candle.close)
    hit = (future > entry) if sig.signal_type == "BUY" else (future < entry)
    return hit, 0.5  # 폴백 평가는 가중치 0.5


async def update_indicator_weights(session: AsyncSession) -> dict[str, int]:
    """최근 시그널 품질을 집계하여 indicator_weights 갱신.

    1순위: quality_score (MFE/MAE 기반, 매도 시점 평가)
    2순위: N시간 후 가격 비교 (폴백)
    """
    result_counts = {"updated": 0, "skipped": 0}

    for tf in EVAL_INTERVALS:
        signals = (await session.execute(
            select(Signal).where(
                Signal.source == "technical",
                Signal.signal_type.in_(["BUY", "SELL"]),
                Signal.timeframe == tf,
                Signal.created_at >= datetime.now(UTC) - timedelta(days=30),
            ).order_by(Signal.created_at)
        )).scalars().all()

        if len(signals) < MIN_SAMPLES:
            result_counts["skipped"] += 1
            continue

        # 지표별 가중 적중률 집계
        ind_stats: dict[str, dict] = {}

        for sig in signals:
            # quality_score 우선, 없으면 폴백
            eval_result = _evaluate_signal_quality(sig)
            if eval_result is None:
                eval_result = await _fallback_evaluate(session, sig, tf)
            if eval_result is None:
                continue

            hit, weight = eval_result
            inds = sig.indicators or {}
            fired = _extract_fired_indicators(inds, sig.signal_type)

            for ind_name in fired:
                if ind_name not in ind_stats:
                    ind_stats[ind_name] = {
                        "weighted_hits": 0.0,
                        "weighted_total": 0.0,
                        "total": 0,
                        "hits": 0,
                        "direction": sig.signal_type,
                    }
                ind_stats[ind_name]["weighted_total"] += weight
                ind_stats[ind_name]["total"] += 1
                if hit:
                    ind_stats[ind_name]["weighted_hits"] += weight
                    ind_stats[ind_name]["hits"] += 1

        tf_weights: dict[str, Weight] = {}
        for ind_name, stats in ind_stats.items():
            if stats["total"] < MIN_SAMPLES:
                continue

            new_accuracy = (
                stats["weighted_hits"] / stats["weighted_total"]
                if stats["weighted_total"] > 0
                else 0.0
            )
            direction = stats["direction"]

            existing = (await session.execute(
                select(IndicatorWeight).where(
                    IndicatorWeight.name == ind_name,
                    IndicatorWeight.direction == direction,
                    IndicatorWeight.timeframe == tf,
                )
            )).scalar_one_or_none()

            if existing:
                updated_acc = (
                    (1 - EMA_ALPHA) * existing.accuracy
                    + EMA_ALPHA * new_accuracy
                )
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
