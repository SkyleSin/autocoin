"""전략 스케줄러 — queue 기반 이벤트 구동.

compute_indicators 완료 시 strategy_queue에 push된 이벤트를 받아
전략을 실행하고, BUY/SELL이면 LLM Job을 생성한다.

수집/분석과 완전 독립. queue만 공유.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.strategy.base import get_strategy
from models.job import Job
from models.job_status import JobStatus
from models.signal import Signal
from services.alert_service import AlertAction, check_alert_preview
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
    """단일 이벤트 처리: 전략 실행 → Signal 저장 → LLM 체이닝."""
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
        candle_count = meta.get("candle_count", 0)
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

        # SELL → 보유량 없으면 LLM 호출 스킵 (API 비용 절감)
        if result.signal_type == "SELL":
            if not await _has_coin_balance(event.market, settings):
                logger.info(
                    "strategy: SELL 스킵 — 보유량 없음: %s", event.market,
                )
                await session.commit()
                return

        # BUY/SELL → 알림 가능 여부 확인 후 LLM Job 생성
        if result.signal_type in ("BUY", "SELL"):
            action = await check_alert_preview(
                session, event.market, strategy_timeframe,
                signal_type=result.signal_type,
            )
            if action in (AlertAction.SKIP, AlertAction.PASSED):
                logger.info(
                    "strategy: LLM skip (alert %s): %s %s %s",
                    action, event.market,
                    strategy_timeframe, result.signal_type,
                )
            else:
                llm_job = Job(
                    type="analyze_local_llm",
                    status=JobStatus.Ready.value,
                    content={
                        "market": event.market,
                        "timeframe": strategy_timeframe,
                        "close_price": close_price,
                        "indicators": combined_indicators,
                        "technical_signal": result.signal_type,
                        "technical_reasons": result.reasons,
                        "candle_count": candle_count,
                    },
                    priority=15,
                )
                session.add(llm_job)
                logger.info(
                    "strategy: LLM job created: %s %s %s",
                    event.market, strategy_timeframe, result.signal_type,
                )

        await session.commit()


async def _has_coin_balance(market: str, settings: Settings) -> bool:
    """빗썸 잔고 조회 → 해당 코인 보유 여부 반환. 실패 시 True(보수적)."""
    try:
        from job_runner.handlers.trade import _accounts_sync

        accounts = await asyncio.to_thread(_accounts_sync, settings)
        coin = market.split("-")[1] if "-" in market else ""
        acc = next((a for a in accounts if a.get("currency") == coin), None)
        return float(acc["balance"]) > 0 if acc else False
    except Exception:
        logger.warning("SELL 보유량 조회 실패, 보수적으로 허용: %s", market)
        return True
