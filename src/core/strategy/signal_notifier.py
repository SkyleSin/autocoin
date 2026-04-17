"""시그널 알림 발송 — strategy_scheduler에서 BUY/SELL 시 텔레그램 알림.

LLM 없이 기술분석 시그널을 직접 텔레그램으로 발송한다.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.strategy.signal_alert_sender import _send_alert
from models.active_alert import ActiveAlert
from services.alert_service import AlertAction, check_alert
from utils.config import Settings

logger = logging.getLogger(__name__)


async def notify_signal(
    session: AsyncSession,
    settings: Settings,
    *,
    market: str,
    timeframe: str,
    signal_type: str,
    confidence: float,
    reasons: list[str],
    indicators: dict | None,
    close_price: float,
) -> None:
    """BUY/SELL 시그널을 텔레그램으로 알림 + 섀도우 기록."""
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return

    # SELL 보유량 확인 + 소액 전량 매도
    force_full_sell = False
    if signal_type == "SELL":
        has_balance, force_full_sell = await _check_sell_balance(
            session, market, timeframe, close_price, settings,
        )
        if not has_balance:
            return

    # AlertAction 판정 (DB 상태 업데이트 포함)
    action = await check_alert(
        session, market, timeframe,
        signal_type=signal_type,
        confidence=confidence,
        reasons=reasons,
        indicators=indicators,
        remind_interval_sec=settings.alert_remind_interval_sec,
    )

    if action not in (AlertAction.NEW, AlertAction.CHANGED, AlertAction.REMIND):
        return

    await _send_alert(
        session, settings,
        market=market, timeframe=timeframe,
        signal_type=signal_type, confidence=confidence,
        reasons=reasons, indicators=indicators,
        close_price=close_price,
        action=action, force_full_sell=force_full_sell,
    )

    # 섀도우 트레이딩
    try:
        from core.shadow.shadow_service import record_shadow_trade

        await record_shadow_trade(
            session,
            market=market,
            signal_type=signal_type,
            close_price=close_price,
            confidence=confidence,
        )
    except Exception:
        logger.warning("shadow trade 기록 실패", exc_info=True)


async def _check_sell_balance(
    session: AsyncSession,
    market: str,
    timeframe: str,
    close_price: float,
    settings: Settings,
) -> tuple[bool, bool]:
    """SELL 보유량 확인. Returns (has_balance, force_full_sell)."""
    try:
        from job_runner.handlers.trade import _accounts_sync

        accounts = await asyncio.to_thread(_accounts_sync, settings)
        coin = market.split("-")[1] if "-" in market else ""
        acc = next((a for a in accounts if a.get("currency") == coin), None)
        balance = float(acc["balance"]) if acc else 0

        if balance <= 0:
            # ActiveAlert 삭제
            existing = (await session.execute(
                select(ActiveAlert).where(
                    ActiveAlert.market == market,
                    ActiveAlert.timeframe == timeframe,
                )
            )).scalar_one_or_none()
            if existing:
                await session.delete(existing)
                logger.info("SELL 보유량 없음 → ActiveAlert 삭제: %s", market)
            return False, False

        krw_value = balance * close_price
        if krw_value <= 5000:
            logger.info("소액 보유 전량 매도 전환: %s (%.0f원)", market, krw_value)
            return True, True

        return True, False
    except Exception:
        logger.warning("SELL 보유 확인 실패, 알림 생략", exc_info=True)
        return False, False
