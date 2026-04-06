"""활성 알림 관리 — 중복 방지, 변경 감지, 재알림 주기 제어."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.active_alert import ActiveAlert

logger = logging.getLogger(__name__)


class AlertAction:
    """알림 판단 결과."""

    NEW = "new"                # 신규 시그널 → 즉시 알림
    CHANGED = "changed"        # 시그널 변경 → 즉시 알림 + 기존 삭제
    REMIND = "remind"          # 동일 시그널 유지, 재알림 시간 도래 → 재알림
    SKIP = "skip"              # 동일 시그널, 재알림 시간 미도래 → 무시
    CLEARED = "cleared"        # HOLD로 전환 → 기존 삭제, 알림 없음
    PASSED = "passed"          # 사용자가 패스, 동일 패턴 유지 → 무시


async def mark_passed(
    session: AsyncSession,
    market: str,
) -> bool:
    """사용자 패스 → ActiveAlert에 passed 마킹. 마켓의 모든 타임프레임 대상."""
    results = (await session.execute(
        select(ActiveAlert).where(ActiveAlert.market == market)
    )).scalars().all()
    if not results:
        return False
    now = datetime.now(UTC)
    for alert in results:
        alert.passed_at = now
        alert.passed_reasons = alert.reasons
        logger.info("alert passed: %s %s", market, alert.timeframe)
    return True


async def check_alert(
    session: AsyncSession,
    market: str,
    timeframe: str,
    signal_type: str,
    confidence: float,
    reasons: list[str],
    indicators: dict | None,
    *,
    remind_interval_sec: float = 600.0,
) -> str:
    """시그널에 대해 알림을 보내야 하는지 판단하고 DB 상태 업데이트.

    Returns:
        AlertAction 상수 (new, changed, remind, skip, cleared)
    """
    existing = (await session.execute(
        select(ActiveAlert).where(
            ActiveAlert.market == market,
            ActiveAlert.timeframe == timeframe,
        )
    )).scalar_one_or_none()

    now = datetime.now(UTC)

    # HOLD → 기존 알림 삭제
    if signal_type == "HOLD":
        if existing:
            await session.delete(existing)
            logger.info("alert cleared: %s %s (was %s)", market, timeframe, existing.signal_type)
            return AlertAction.CLEARED
        return AlertAction.SKIP

    # 기존 알림 없음 → 신규
    if existing is None:
        session.add(ActiveAlert(
            market=market,
            timeframe=timeframe,
            signal_type=signal_type,
            confidence=confidence,
            reasons=json.dumps(reasons, ensure_ascii=False),
            indicators=indicators,
            last_alerted_at=now,
        ))
        logger.info("alert new: %s %s %s", market, timeframe, signal_type)
        return AlertAction.NEW

    # 시그널 변경 (BUY→SELL, SELL→BUY)
    if existing.signal_type != signal_type:
        old_signal = existing.signal_type
        existing.signal_type = signal_type
        existing.confidence = confidence
        existing.reasons = json.dumps(reasons, ensure_ascii=False)
        existing.indicators = indicators
        existing.last_alerted_at = now
        logger.info(
            "alert changed: %s %s %s→%s",
            market, timeframe, old_signal, signal_type,
        )
        return AlertAction.CHANGED

    # 동일 시그널 — 패스 상태 확인
    if existing.passed_at is not None:
        new_reasons = json.dumps(reasons, ensure_ascii=False)
        if existing.passed_reasons == new_reasons:
            # 동일 패턴 유지 → 패스 유지
            return AlertAction.PASSED
        # 패턴 변경 → 패스 해제, 새 알림
        existing.passed_at = None
        existing.passed_reasons = None
        existing.confidence = confidence
        existing.reasons = new_reasons
        existing.indicators = indicators
        existing.last_alerted_at = now
        logger.info(
            "alert pass cleared (pattern changed): %s %s %s",
            market, timeframe, signal_type,
        )
        return AlertAction.CHANGED

    # 재알림 시간 확인
    elapsed = (now - existing.last_alerted_at).total_seconds()
    if elapsed >= remind_interval_sec:
        existing.confidence = confidence
        existing.reasons = json.dumps(reasons, ensure_ascii=False)
        existing.indicators = indicators
        existing.last_alerted_at = now
        logger.info(
            "alert remind: %s %s %s (%.0fs elapsed)",
            market, timeframe, signal_type, elapsed,
        )
        return AlertAction.REMIND

    # 아직 재알림 시간 안 됨
    return AlertAction.SKIP
