"""분석 핸들러 — analyze_local_llm.

analyze_technical은 strategy_scheduler로 이관됨.
기존 Job 호환을 위해 핸들러는 유지하되 no-op 처리.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.analysis.chart import generate_chart_from_db_candles
from core.strategy.llm.prompts import build_analysis_prompt
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


async def handle_analyze_technical(
    job: Job,
    settings: Settings,
    *,
    session: AsyncSession,
) -> None:
    """레거시 호환용. strategy_scheduler로 이관됨."""
    logger.warning(
        "analyze_technical job은 더 이상 사용되지 않습니다 (job_id=%s)",
        job.id,
    )
    job.response = {"status": "deprecated", "reason": "moved to strategy_scheduler"}
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

    candle_count = int(job.content.get("candle_count", 0))

    prompt = build_analysis_prompt(
        market, close_price, indicators, tech_signal, tech_reasons,
        timeframe=timeframe, candle_count=candle_count,
    )

    llm_result = None
    llm_source = "local_llm"

    # DeepSeek 우선 시도 → 실패 시 Ollama 폴백
    if settings.deepseek_api_key:
        try:
            from core.strategy.llm.deepseek_llm import query_deepseek

            llm_result = await query_deepseek(
                prompt,
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                timeout=settings.deepseek_timeout,
            )
            llm_source = "deepseek"
        except Exception:
            logger.warning("DeepSeek API 실패, Ollama 폴백", exc_info=True)

    if llm_result is None:
        from core.strategy.llm.local_llm import query_ollama

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
        "llm_source": llm_source,
    }

    signal = Signal(
        market=market,
        timeframe=timeframe,
        signal_type=llm_result.decision,
        confidence=llm_result.confidence,
        indicators=enriched_indicators,
        source=llm_source,
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

    # HOLD 판단 → 간략 알림 전송 (시스템 동작 확인용)
    if llm_result.decision == "HOLD":
        coin = market.replace("KRW-", "")
        pat_name = "-"
        if indicators and isinstance(indicators.get("patterns"), dict):
            pats = indicators["patterns"].get("patterns", [])
            if pats:
                pat_name = pats[0].get("name_ko", "-")
        hold_text = (
            f"⏸ {coin} | HOLD ({llm_result.confidence:.0%})"
            f" | {pat_name} — {llm_source}\n"
            f"1차: {tech_signal} | {', '.join(tech_reasons[:3])}\n"
            f"LLM: {llm_result.reason[:80]}"
        )
        from telegram.bot import send_message

        await send_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            hold_text,
        )
        return

    # SELL 시그널 → 보유량 확인을 check_alert 앞에서 수행
    # 보유량 없으면 ActiveAlert 생성 방지 + 기존 것 삭제
    # SELL 보유량 확인 + 소액 전량 매도 플래그
    _force_full_sell = False
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
            # 소액 보유 → 전량 매도 전환 (최소주문금액 미달 방지)
            krw_value = coin_balance * close_price
            if krw_value <= 5000:
                _force_full_sell = True
                logger.info(
                    "소액 보유 전량 매도 전환: %s (%.0f원)", market, krw_value,
                )
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
        llm_source=llm_source,
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
        from core.strategy.executor.position_sizing import calculate_sell_recommendation

        pat_list = None
        if indicators and isinstance(indicators.get("patterns"), dict):
            pat_list = indicators["patterns"].get("patterns", [])
        sell_rec = calculate_sell_recommendation(pat_list, confidence=llm_result.confidence)
        sell_rec_pct = sell_rec.recommended_pct
        sell_choices = sell_rec.choices
        # 소액 보유 시 전량 매도 강제 (최소주문금액 미달 방지)
        if _force_full_sell and sell_rec_pct < 100:
            sell_rec_pct = 100
            sell_choices = None
        alert_text += f"\n\n📊 *매도 추천:* {sell_rec.pattern_name} → `{sell_rec_pct}%`"
        if sell_rec_pct < 100:
            alert_text += " (부분 매도 → 재진입 가능)"

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

    # 섀도우 트레이딩 (자동화 가정 가상 매매 기록)
    if llm_result.decision in ("BUY", "SELL") and session is not None:
        try:
            from core.shadow.shadow_service import record_shadow_trade
            await record_shadow_trade(
                session,
                market=market,
                signal_type=llm_result.decision,
                close_price=close_price,
                confidence=llm_result.confidence,
                signal_id=signal.id if signal else None,
            )
        except Exception:
            logger.warning("shadow trade 기록 실패", exc_info=True)
