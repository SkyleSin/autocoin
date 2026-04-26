"""분석 루프 — 10초 주기: ct-api 봉 조회 → 지표/SR/패턴 계산 → 스냅샷 저장 → 전략 평가."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import pandas as pd
from analysis.indicators import compute_all
from analysis.patterns import analyze as analyze_patterns
from analysis.signal_generator import evaluate as evaluate_signal
from analysis.support_resistance import analyze as analyze_sr
from infra.api.candle import get_candles
from infra.api.indicator_snapshot import upsert_indicator_snapshot
from infra.api.tracked_asset import get_active_markets
from utils.config import TF_PERIODS, Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 지표 분석
# ---------------------------------------------------------------------------

async def _analyze_market(
    market: str, timeframe: str, last_open_at: str | None,
) -> str | None:
    """ct-api에서 봉 조회 → 지표/SR/패턴 계산 → 스냅샷 저장.

    Returns:
        분석된 최신 봉의 open_at 값 (새 봉이 없으면 None).
    """
    candles = await get_candles(market, timeframe, limit=200, order="desc")
    if not candles:
        return None

    latest_open_at = candles[0].get("open_at")
    if latest_open_at == last_open_at:
        return None  # 새 봉 없음 → 분석 스킵

    # desc → asc 정렬
    candles_asc = list(reversed(candles))

    closes = pd.Series([float(c["close"]) for c in candles_asc])
    highs = pd.Series([float(c["high"]) for c in candles_asc])
    lows = pd.Series([float(c["low"]) for c in candles_asc])
    volumes = pd.Series([float(c.get("volume", 0)) for c in candles_asc])
    close_price = float(closes.iloc[-1])

    # 지표 계산
    snap = compute_all(closes, volumes)
    if snap is None:
        logger.debug("지표 계산 불가 (데이터 부족): %s %s", market, timeframe)
        return None  # 재시도 가능하도록 None 반환

    # S/R 분석
    try:
        sr = analyze_sr(highs, lows, closes)
    except Exception:
        logger.exception("SR 분석 실패: %s %s", market, timeframe)
        sr = None

    # 시그널 평가
    try:
        signal_result = evaluate_signal(
            snap, close_price, timeframe=timeframe, sr_analysis=sr,
        )
    except Exception:
        logger.exception("시그널 평가 실패: %s %s", market, timeframe)
        signal_result = None

    # 패턴 분석
    try:
        pattern_result = analyze_patterns(
            highs, lows, closes, timeframe=timeframe, volumes=volumes,
        )
    except Exception:
        logger.exception("패턴 분석 실패: %s %s", market, timeframe)
        pattern_result = None

    # 스냅샷 저장 — indicators JSONB에 모든 분석 결과 포함
    indicators_dict: dict = {
        **snap.to_dict(),
        "close_price": close_price,
        "support_resistance": sr.to_dict() if sr else {},
        "patterns": pattern_result.to_dict() if pattern_result else {},
        "signal": signal_result.signal_type if signal_result else "HOLD",
        "confidence": signal_result.confidence if signal_result else 0.0,
        "reasons": signal_result.reasons if signal_result else [],
    }
    payload: dict = {
        "market": market,
        "timeframe": timeframe,
        "candle_open_at": latest_open_at,
        "indicators": indicators_dict,
    }
    try:
        await upsert_indicator_snapshot(payload)
        logger.debug("snapshot 저장: %s %s signal=%s", market, timeframe,
                     indicators_dict["signal"])
    except Exception:
        logger.exception("snapshot 저장 실패: %s %s", market, timeframe)
        return None

    return latest_open_at


# ---------------------------------------------------------------------------
# 메인 루프
# ---------------------------------------------------------------------------

async def analysis_loop(settings: Settings) -> None:
    """분석 루프 (10초 주기).

    ct-api에서 봉을 조회하여 지표/SR/패턴을 계산하고 스냅샷을 저장한다.
    15m 분석 완료 시 전략 평가를 직접 호출한다.
    """
    logger.info("analysis_loop: 시작")

    from strategy.strategy_scheduler import handle_analysis_complete

    # 중복 분석 방지: (market, timeframe) → 마지막 분석 open_at
    last_open_at: dict[tuple[str, str], str | None] = {}

    try:
        while True:
            tick_start = datetime.now(UTC)

            # 활성 마켓 조회
            try:
                assets = await get_active_markets()
                markets = [a["market"] for a in assets] if assets else []
            except Exception:
                logger.exception("analysis: 마켓 조회 실패")
                markets = []

            # 분석: ct-api 봉 조회 → 지표/신호 저장
            for tf in TF_PERIODS:
                for market in markets:
                    key = (market, tf)
                    try:
                        new_open_at = await _analyze_market(
                            market, tf, last_open_at.get(key),
                        )
                        if new_open_at:
                            last_open_at[key] = new_open_at

                            # 15m 분석 완료 → 전략 평가 직접 호출
                            if tf == "15m":
                                try:
                                    await handle_analysis_complete(
                                        market, tf, settings,
                                    )
                                except Exception:
                                    logger.exception(
                                        "전략 평가 실패: %s %s", market, tf,
                                    )
                    except Exception:
                        logger.exception("분석 실패: %s %s", market, tf)

            # 다음 틱까지 대기 (10초 주기)
            elapsed = (datetime.now(UTC) - tick_start).total_seconds()
            await asyncio.sleep(max(0.0, 10.0 - elapsed))

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("analysis_loop: 종료")
