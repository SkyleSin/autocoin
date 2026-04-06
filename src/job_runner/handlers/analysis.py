"""분석 핸들러 — analyze_technical, analyze_local_llm."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.local_llm import query_ollama
from ai_engine.prompts import build_analysis_prompt
from analysis.chart import generate_chart_from_db_candles
from analysis.indicators import compute_all
from analysis.patterns import analyze as analyze_patterns
from analysis.signal_generator import evaluate
from analysis.support_resistance import analyze as analyze_sr
from models.active_alert import ActiveAlert
from models.candle import Candle
from models.job import Job
from models.job_status import JobStatus
from models.signal import Signal
from services.alert_service import AlertAction, check_alert
from telegram.bot import send_signal_alert
from telegram.messages import format_combined_alert
from utils.config import Settings

logger = logging.getLogger(__name__)


async def _run_technical_analysis(
    session: AsyncSession,
    market: str,
    timeframe: str,
    candle_limit: int = 200,
) -> dict[str, Any]:
    """DB에서 캔들 로드 → 지표 계산 → Signal 저장 → 결과 반환."""
    result_rows = await session.execute(
        select(Candle)
        .where(Candle.market == market, Candle.timeframe == timeframe)
        .order_by(Candle.open_at.desc())
        .limit(candle_limit)
    )
    candles = list(reversed(result_rows.scalars().all()))

    if len(candles) < 26:
        return {
            "status": "insufficient_data",
            "candle_count": len(candles),
            "required": 26,
        }

    highs = pd.Series([float(c.high) for c in candles])
    lows = pd.Series([float(c.low) for c in candles])
    closes = pd.Series([float(c.close) for c in candles])

    snap = compute_all(closes)
    if snap is None:
        return {"status": "compute_failed", "candle_count": len(candles)}

    pat = analyze_patterns(highs, lows, closes)
    sr = analyze_sr(highs, lows, closes)

    close_price = float(candles[-1].close)
    signal_result = evaluate(
        snap, close_price, pattern_analysis=pat, sr_analysis=sr,
    )

    combined_indicators = {
        **snap.to_dict(),
        "patterns": pat.to_dict(),
        "support_resistance": sr.to_dict(),
    }

    signal = Signal(
        market=market,
        timeframe=timeframe,
        signal_type=signal_result.signal_type,
        confidence=signal_result.confidence,
        indicators=combined_indicators,
        source="technical",
    )
    session.add(signal)

    # BUY/SELL 시그널 → analyze_local_llm Job 자동 생성
    chain_created = False
    if signal_result.signal_type in ("BUY", "SELL"):
        llm_job = Job(
            type="analyze_local_llm",
            status=JobStatus.Ready.value,
            content={
                "market": market,
                "timeframe": timeframe,
                "close_price": close_price,
                "indicators": combined_indicators,
                "technical_signal": signal_result.signal_type,
                "technical_reasons": signal_result.reasons,
            },
            priority=15,
        )
        session.add(llm_job)
        chain_created = True

    return {
        "status": "ok",
        "candle_count": len(candles),
        "signal_type": signal_result.signal_type,
        "confidence": signal_result.confidence,
        "reasons": signal_result.reasons,
        "indicators": snap.to_dict(),
        "llm_chain": chain_created,
    }


async def handle_analyze_technical(
    job: Job,
    settings: Settings,
    *,
    session: AsyncSession,
) -> None:
    if not job.content or not str(job.content.get("market", "")).strip():
        raise ValueError("analyze_technical 잡에 content.market 이 필요합니다.")
    market = str(job.content["market"]).strip()
    timeframe = str(job.content.get("timeframe", "1m"))
    result = await _run_technical_analysis(session, market, timeframe)
    job.response = result
    job.status = JobStatus.Complete.value


async def handle_analyze_local_llm(
    job: Job,
    settings: Settings,
    *,
    session: AsyncSession,
) -> None:
    if not job.content:
        raise ValueError("analyze_local_llm 잡에 content가 필요합니다.")
    market = str(job.content["market"]).strip()
    timeframe = str(job.content.get("timeframe", "1m"))
    close_price = float(job.content["close_price"])
    indicators = job.content["indicators"]
    tech_signal = str(job.content["technical_signal"])
    tech_reasons = list(job.content.get("technical_reasons", []))

    prompt = build_analysis_prompt(
        market, close_price, indicators, tech_signal, tech_reasons,
    )
    llm_result = await query_ollama(
        prompt,
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout=settings.ollama_timeout,
        num_gpu=settings.ollama_num_gpu,
    )

    enriched_indicators = {
        **(indicators or {}),
        "llm_reason": llm_result.reason,
        "tech_reasons": tech_reasons,
        "llm_raw_response": llm_result.raw_response[:1000],
    }

    signal = Signal(
        market=market,
        timeframe=timeframe,
        signal_type=llm_result.decision,
        confidence=llm_result.confidence,
        indicators=enriched_indicators,
        source="local_llm",
    )
    session.add(signal)

    job.response = {
        "decision": llm_result.decision,
        "confidence": llm_result.confidence,
        "reason": llm_result.reason,
        "raw_response": llm_result.raw_response[:1000],
    }
    job.status = JobStatus.Complete.value

    # 알림 관리
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return

    # SELL 시그널 → 보유량 확인을 check_alert 앞에서 수행
    # 보유량 없으면 ActiveAlert 생성 방지 + 기존 것 삭제
    if llm_result.decision == "SELL":
        try:
            from job_runner.handlers.trade import _accounts_sync

            accounts = await asyncio.to_thread(_accounts_sync, settings)
            coin_currency = market.split("-")[1] if "-" in market else ""
            coin_acc = next(
                (a for a in accounts if a.get("currency") == coin_currency),
                None,
            )
            coin_balance = float(coin_acc["balance"]) if coin_acc else 0
            if coin_balance <= 0:
                existing = (await session.execute(
                    select(ActiveAlert).where(
                        ActiveAlert.market == market,
                        ActiveAlert.timeframe == timeframe,
                    )
                )).scalar_one_or_none()
                if existing:
                    await session.delete(existing)
                    logger.info(
                        "SELL 보유량 없음 → ActiveAlert 삭제: %s %s", market, timeframe,
                    )
                else:
                    logger.info("SELL 알림 생략: %s 보유량 없음", market)
                return
        except Exception:
            logger.warning("SELL 보유 확인 실패, 알림 생략", exc_info=True)
            return

    action = await check_alert(
        session, market, timeframe,
        signal_type=llm_result.decision,
        confidence=llm_result.confidence,
        reasons=tech_reasons,
        indicators=indicators,
        remind_interval_sec=settings.alert_remind_interval_sec,
    )

    if action not in (AlertAction.NEW, AlertAction.CHANGED, AlertAction.REMIND):
        return

    tech_conf = min(len(tech_reasons) * 0.25, 1.0)
    prefix = ""
    if action == AlertAction.CHANGED:
        prefix = "🔄 *시그널 변경!*\n\n"
    elif action == AlertAction.REMIND:
        prefix = "🔔 *재알림*\n\n"

    alert_text = prefix + format_combined_alert(
        market=market,
        close_price=close_price,
        tech_signal=tech_signal,
        tech_confidence=tech_conf,
        tech_reasons=tech_reasons,
        llm_decision=llm_result.decision,
        llm_confidence=llm_result.confidence,
        llm_reason=llm_result.reason,
        indicators=indicators,
    )

    chart_image: bytes | None = None
    try:
        candle_rows = list(reversed((await session.execute(
            select(Candle)
            .where(Candle.market == market, Candle.timeframe == timeframe)
            .order_by(Candle.open_at.desc())
            .limit(200)
        )).scalars().all()))
        if len(candle_rows) >= 30:
            chart_image = generate_chart_from_db_candles(
                list(candle_rows),
                market=market,
                signal_type=llm_result.decision,
                sr_analysis=indicators.get("support_resistance"),
                pattern_analysis=indicators.get("patterns"),
            )
    except Exception:
        logger.warning("차트 이미지 생성 실패", exc_info=True)

    # SELL 시 매도 추천 비율 계산
    sell_rec_pct = 0
    sell_choices = None
    if llm_result.decision == "SELL":
        from executor.position_sizing import calculate_sell_recommendation

        pat_list = None
        if indicators and isinstance(indicators.get("patterns"), dict):
            pat_list = indicators["patterns"].get("patterns", [])
        sell_rec = calculate_sell_recommendation(pat_list)
        sell_rec_pct = sell_rec.recommended_pct
        sell_choices = sell_rec.choices
        alert_text += f"\n\n📊 *매도 추천:* {sell_rec.pattern_name} → `{sell_rec_pct}%`"

    await send_signal_alert(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        alert_text,
        chart_image=chart_image,
        market=market,
        signal_type=llm_result.decision,
        close_price=close_price,
        sell_recommended_pct=sell_rec_pct,
        sell_choices=sell_choices,
    )
