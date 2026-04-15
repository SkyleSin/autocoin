"""5분봉 트리거 전략 — 기존 signal_generator 기반.

베이지안 적중률 결합 + S/R 부스트 + MTF 필터.

파이프라인 (indicator_snapshots에서 지표 조회):
  1) 메인TF 지표 → 베이지안 시그널
  2) 15m → 반대 방향 충돌 필터
  3) 1h  → 동의 부스트(×1.1) / 반대 감쇠(×0.85)
  4) 4h/1h 추세 → 하락 중 BUY → HOLD
  5) BTC 일봉 → bear_market 플래그 (LLM 전달용)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pandas as pd
from sqlalchemy import select

from core.analysis.indicators import IndicatorSnapshot as IndicatorSnap
from core.analysis.signal_generator import SignalResult, evaluate
from core.analysis.support_resistance import SRAnalysis
from core.analysis.trend import get_higher_trend
from models.candle import Candle
from models.indicator_snapshot import IndicatorSnapshot

from .base import BaseStrategy, StrategyResult, register_strategy

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from utils.config import Settings

logger = logging.getLogger(__name__)


async def _get_latest_snapshot(
    session: AsyncSession,
    market: str,
    timeframe: str,
) -> dict[str, Any] | None:
    """indicator_snapshots에서 최신 지표 조회."""
    row = (await session.execute(
        select(IndicatorSnapshot)
        .where(
            IndicatorSnapshot.market == market,
            IndicatorSnapshot.timeframe == timeframe,
        )
        .order_by(IndicatorSnapshot.candle_open_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    if row is None:
        return None
    return row.indicators


def _rebuild_snap(indicators: dict[str, Any]) -> IndicatorSnap | None:
    """indicator_snapshots의 JSON → IndicatorSnapshot 복원."""
    try:
        return IndicatorSnap(
            rsi_14=indicators.get("rsi_14"),
            macd=indicators.get("macd"),
            macd_signal=indicators.get("macd_signal"),
            macd_histogram=indicators.get("macd_histogram"),
            macd_histogram_prev=indicators.get("macd_histogram_prev"),
            bb_upper=indicators.get("bb_upper"),
            bb_middle=indicators.get("bb_middle"),
            bb_lower=indicators.get("bb_lower"),
            sma_20=indicators.get("sma_20"),
            sma_50=indicators.get("sma_50"),
            ema_12=indicators.get("ema_12"),
            ema_26=indicators.get("ema_26"),
            volume_ratio=indicators.get("volume_ratio"),
        )
    except Exception:
        return None


def _rebuild_sr(sr_dict: dict[str, Any] | None) -> SRAnalysis | None:
    """indicator_snapshots의 S/R JSON → SRAnalysis 복원."""
    if not sr_dict:
        return None
    from core.analysis.support_resistance import PriceLevel

    levels = [
        PriceLevel(
            price=lv["price"],
            level_type=lv["type"],
            touch_count=lv["touches"],
            strength=lv.get("strength", 0.5),
        )
        for lv in sr_dict.get("levels", [])
    ]
    return SRAnalysis(
        levels=levels,
        nearest_support=sr_dict.get("nearest_support"),
        nearest_resistance=sr_dict.get("nearest_resistance"),
        signal=sr_dict.get("signal", "NONE"),
        distance_to_support_pct=sr_dict.get("distance_to_support_pct"),
        distance_to_resistance_pct=sr_dict.get("distance_to_resistance_pct"),
    )


async def _evaluate_tf(
    session: AsyncSession,
    market: str,
    timeframe: str,
) -> tuple[SignalResult | None, dict[str, Any] | None]:
    """특정 TF의 indicator_snapshots → 시그널 평가. (None이면 데이터 없음)"""
    data = await _get_latest_snapshot(session, market, timeframe)
    if data is None:
        return None, None

    snap = _rebuild_snap(data)
    if snap is None:
        return None, None

    close_price = data.get("close_price", 0.0)
    sr = _rebuild_sr(data.get("support_resistance"))
    signal = evaluate(snap, close_price, timeframe=timeframe, sr_analysis=sr)

    detail = {
        "timeframe": timeframe,
        "signal_type": signal.signal_type,
        "confidence": signal.confidence,
        "reasons": signal.reasons,
        "indicators": snap.to_dict(),
    }
    return signal, detail


async def _check_bear_market(session: AsyncSession) -> bool:
    """BTC 일봉 종가 < SMA50이면 하락장."""
    data = await _get_latest_snapshot(session, "KRW-BTC", "1d")
    if data is None:
        # indicator_snapshots에 없으면 캔들에서 직접 계산
        rows = await session.execute(
            select(Candle)
            .where(Candle.market == "KRW-BTC", Candle.timeframe == "1d")
            .order_by(Candle.open_at.desc())
            .limit(60)
        )
        candles = list(reversed(rows.scalars().all()))
        if len(candles) < 50:
            return False
        closes = pd.Series([float(c.close) for c in candles])
        sma50 = closes.rolling(50).mean()
        if pd.isna(sma50.iloc[-1]):
            return False
        return float(closes.iloc[-1]) < float(sma50.iloc[-1])

    # indicator_snapshots에서 SMA50 직접 비교
    sma_50 = data.get("sma_50")
    close = data.get("close_price")
    if sma_50 is None or close is None:
        return False

    is_bear = close < sma_50
    if is_bear:
        logger.info("bear_market: BTC %.0f < SMA50 %.0f", close, sma_50)
    return is_bear


@register_strategy
class Strategy5mTrigger(BaseStrategy):
    name = "5m_trigger"
    description = "5분봉 베이지안 시그널 + S/R 부스트 + MTF 필터"

    def required_timeframes(self) -> list[str]:
        return ["5m", "15m", "1h", "4h"]

    async def evaluate(
        self,
        session: AsyncSession,
        market: str,
        settings: Settings,
    ) -> StrategyResult:
        """indicator_snapshots에서 지표를 읽어 시그널 판단.

        Returns:
            StrategyResult — metadata에 indicators, close_price 등
            후속 처리용 데이터 포함.
        """
        # ── 1단계: 메인TF 시그널 (indicator_snapshots에서 조회) ──
        timeframe = "5m"
        main_data = await _get_latest_snapshot(session, market, timeframe)

        if main_data is None:
            return StrategyResult(
                signal_type="HOLD",
                confidence=0.0,
                reasons=["메인TF 지표 없음"],
                metadata={"status": "insufficient_data"},
            )

        snap = _rebuild_snap(main_data)
        if snap is None:
            return StrategyResult(
                signal_type="HOLD",
                confidence=0.0,
                reasons=["지표 복원 실패"],
                metadata={"status": "compute_failed"},
            )

        close_price = main_data.get("close_price", 0.0)
        sr = _rebuild_sr(main_data.get("support_resistance"))
        signal_result = evaluate(
            snap, close_price, timeframe=timeframe, sr_analysis=sr,
        )

        # 메타데이터 구성 (compute_indicators에서 저장한 전체 지표 재활용)
        combined_indicators: dict[str, Any] = dict(main_data)

        # ── 2~4단계: MTF 필터 (BUY/SELL일 때만) ──
        ref_tf_str = settings.candle_ref_timeframes
        ref_timeframes = [
            tf.strip() for tf in ref_tf_str.split(",") if tf.strip()
        ]

        if signal_result.signal_type in ("BUY", "SELL") and ref_timeframes:
            # 상위 추세 (4h/1h)
            trend = await get_higher_trend(session, market, ref_timeframes)
            combined_indicators["higher_trend"] = {
                "direction": trend.direction,
                "timeframe": trend.timeframe,
                "sma_20": trend.sma_20,
                "sma_50": trend.sma_50,
                "description": trend.description,
            }

            # 15분봉 충돌 필터 + 1시간봉 부스트/감쇠
            mtf_info: dict[str, Any] = {
                "conflict": False,
                "1h_agree": False,
                "1h_oppose": False,
                "detail": "",
            }

            for tf in ["15m", "1h"]:
                tf_signal, tf_detail = await _evaluate_tf(
                    session, market, tf,
                )
                if tf_detail:
                    combined_indicators[f"tf_{tf}"] = tf_detail

                if tf_signal is None or tf_signal.signal_type == "HOLD":
                    continue

                if tf == "15m":
                    if tf_signal.signal_type != signal_result.signal_type:
                        mtf_info["conflict"] = True
                        mtf_info["detail"] = (
                            f"15분봉 {tf_signal.signal_type} "
                            f"({tf_signal.confidence:.1%}) vs "
                            f"5분봉 {signal_result.signal_type}"
                        )
                        break
                elif tf == "1h":
                    if tf_signal.signal_type == signal_result.signal_type:
                        mtf_info["1h_agree"] = True
                    else:
                        mtf_info["1h_oppose"] = True

            combined_indicators["mtf_filter"] = mtf_info

            if mtf_info["conflict"]:
                signal_result = SignalResult(
                    "HOLD", 0.0,
                    signal_result.reasons
                    + [f"⚠️ 15분봉 반대 방향 ({mtf_info['detail']}) — 보류"],
                )
            else:
                if mtf_info["1h_agree"]:
                    boosted = min(signal_result.confidence * 1.1, 1.0)
                    signal_result = SignalResult(
                        signal_result.signal_type,
                        round(boosted, 4),
                        signal_result.reasons + [
                            f"✅ 1시간봉 동의 — 신뢰도 부스트 "
                            f"{signal_result.confidence:.1%}→{boosted:.1%}"
                        ],
                    )
                elif mtf_info["1h_oppose"]:
                    dampened = signal_result.confidence * 0.85
                    signal_result = SignalResult(
                        signal_result.signal_type,
                        round(dampened, 4),
                        signal_result.reasons + [
                            f"⚠️ 1시간봉 반대 — 신뢰도 감쇠 "
                            f"{signal_result.confidence:.1%}→{dampened:.1%}"
                        ],
                    )

                if (
                    signal_result.signal_type == "BUY"
                    and trend.direction == "DOWN"
                ):
                    signal_result = SignalResult(
                        "HOLD", 0.0,
                        signal_result.reasons + [
                            f"⚠️ 상위 추세 하락 ({trend.description})"
                            " — 매수 보류"
                        ],
                    )

        # ── 5단계: 하락장 플래그 ──
        if signal_result.signal_type in ("BUY", "SELL"):
            is_bear = await _check_bear_market(session)
            combined_indicators["bear_market"] = is_bear

        return StrategyResult(
            signal_type=signal_result.signal_type,
            confidence=signal_result.confidence,
            reasons=signal_result.reasons,
            metadata={
                "status": "ok",
                "timeframe": timeframe,
                "candle_count": main_data.get("candle_count", 0),
                "close_price": close_price,
                "indicators": combined_indicators,
            },
        )
