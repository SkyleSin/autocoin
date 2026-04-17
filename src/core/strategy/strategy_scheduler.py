"""전략 스케줄러 — queue 기반 이벤트 구동.

compute_indicators 완료 시 strategy_queue에 push된 이벤트를 받아
전략을 실행하고, BUY/SELL이면 직접 텔레그램 알림을 발송한다.

수집/분석과 완전 독립. queue만 공유.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.strategy.base import get_strategy
from core.strategy.signal_notifier import notify_signal
from models.signal import Signal
from utils.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyEvent:
    """전략 실행 이벤트 — compute_indicators 완료 후 push."""

    market: str
    timeframe: str


async def strategy_scheduler(
    queue: asyncio.Queue[StrategyEvent],
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """전략 스케줄러 메인 루프. queue에서 이벤트를 꺼내 전략 실행."""
    logger.info("strategy_scheduler: started")

    try:
        while True:
            event = await queue.get()
            try:
                await _handle_event(event, session_factory, settings)
            except Exception:
                logger.exception(
                    "strategy_scheduler: error processing %s %s",
                    event.market, event.timeframe,
                )
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("strategy_scheduler: stopped")


async def _handle_event(
    event: StrategyEvent,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """단일 이벤트 처리: 전략 실행 → Signal 저장 → 알림 발송."""
    strategy = get_strategy()

    async with session_factory() as session:
        result = await strategy.evaluate(session, event.market, settings)
        meta = result.metadata or {}

        if meta.get("status") in ("insufficient_data", "compute_failed"):
            logger.debug(
                "strategy: %s %s skipped (%s)",
                event.market, event.timeframe, meta.get("status"),
            )
            return

        close_price = meta.get("close_price", 0.0)
        combined_indicators = meta.get("indicators", {})
        strategy_timeframe = meta.get("timeframe", event.timeframe)

        # Signal DB 저장 (entry_price 설정 + MFE/MAE 초기값)
        signal = Signal(
            market=event.market,
            timeframe=strategy_timeframe,
            signal_type=result.signal_type,
            confidence=result.confidence,
            indicators=combined_indicators,
            source="technical",
            entry_price=close_price if result.signal_type in ("BUY", "SELL") else None,
            mfe=close_price if result.signal_type in ("BUY", "SELL") else None,
            mae=close_price if result.signal_type in ("BUY", "SELL") else None,
        )
        session.add(signal)

        # BUY/SELL → 반대 방향 시그널 종료 (MFE/MAE 평가)
        if result.signal_type in ("BUY", "SELL") and close_price > 0:
            from services.mfe_mae_tracker import close_opposite_signals

            closed = await close_opposite_signals(
                session, event.market, result.signal_type, close_price,
            )
            if closed:
                logger.info(
                    "strategy: closed %d opposite signals for %s",
                    closed, event.market,
                )

        logger.info(
            "strategy: %s %s → %s (conf=%.2f)",
            event.market, strategy_timeframe,
            result.signal_type, result.confidence,
        )

        # 방어 모드 활성 시 BUY 차단
        if result.signal_type == "BUY":
            from core.safety.defense_mode import is_defense_active

            if is_defense_active(event.market):
                logger.info(
                    "strategy: BUY 차단 — 방어 모드 활성: %s", event.market,
                )
                await session.commit()
                return

        # BUY/SELL → 직접 텔레그램 알림 발송 (SELL 보유량은 notifier에서 확인)
        if result.signal_type in ("BUY", "SELL"):
            await notify_signal(
                session, settings,
                market=event.market,
                timeframe=strategy_timeframe,
                signal_type=result.signal_type,
                confidence=result.confidence,
                reasons=result.reasons,
                indicators=combined_indicators,
                close_price=close_price,
            )

        await session.commit()
