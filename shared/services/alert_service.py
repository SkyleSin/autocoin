"""활성 알림 관리 — 중복 방지, 변경 감지, 재알림 주기 제어."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.active_alert import ActiveAlert

logger = logging.getLogger(__name__)

# 패스 해제 기준: confidence 변화량이 이 값 이상이면 시장 상황 변경으로 간주
_PASS_CONFIDENCE_THRESHOLD = 0.15


def _pass_fingerprint(indicators: dict | None, confidence: float) -> str:
    """패스 비교용 핑거프린트 생성. S/R 시그널 + confidence 기반."""
    sr = "NONE"
    if indicators:
        sr_data = indicators.get("support_resistance")
        if isinstance(sr_data, dict):
            sr = sr_data.get("signal", "NONE")
    return json.dumps({"sr": sr, "conf": round(confidence, 2)}, sort_keys=True)


def _should_clear_pass(old_fp_str: str | None, new_fp_str: str) -> bool:
    """패스 상태를 해제해야 하는지 판단.

    해제 조건:
    - S/R 시그널 변경 (지지 반등→저항 돌파 등)
    - confidence 유의미 상승 (>=0.15)
    """
    if not old_fp_str:
        return True
    try:
        old = json.loads(old_fp_str)
        new = json.loads(new_fp_str)
    except (json.JSONDecodeError, TypeError):
        return True  # 파싱 실패 → 안전하게 해제
    if not isinstance(old, dict) or not isinstance(new, dict):
        return True  # old format(list 등) → 안전하게 해제
    if old.get("sr") != new.get("sr"):
        return True
    old_conf = old.get("conf", 0.0)
    new_conf = new.get("conf", 0.0)
    return (new_conf - old_conf) >= _PASS_CONFIDENCE_THRESHOLD


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
        alert.passed_reasons = _pass_fingerprint(alert.indicators, alert.confidence)
        logger.info("alert passed: %s %s", market, alert.timeframe)
    return True


async def check_alert_preview(
    session: AsyncSession,
    market: str,
    timeframe: str,
    *,
    signal_type: str,
) -> str:
    """LLM 호출 전 알림 가능 여부만 간단 확인 (DB 변경 없음).

    Returns:
        AlertAction 상수 (SKIP, PASSED, 또는 NEW/CHANGED/REMIND 중 하나)
    """
    if signal_type == "HOLD":
        return AlertAction.SKIP

    existing = (await session.execute(
        select(ActiveAlert).where(
            ActiveAlert.market == market,
            ActiveAlert.timeframe == timeframe,
        )
    )).scalar_one_or_none()

    if existing is None:
        return AlertAction.NEW

    # 매매 실행 완료 + 동일 시그널 → SKIP
    if existing.acted_at is not None and existing.signal_type == signal_type:
        return AlertAction.SKIP

    # 시그널 변경 → 쿨다운 확인
    now = datetime.now(UTC)
    if existing.signal_type != signal_type:
        elapsed = (now - existing.last_alerted_at).total_seconds()
        if elapsed < 600.0:  # 기본 쿨다운 (실제 check_alert에서 정확히 판단)
            return AlertAction.SKIP
        return AlertAction.CHANGED

    # 동일 시그널 + 패스 상태
    if existing.passed_at is not None:
        return AlertAction.PASSED

    # 재알림 시간 확인
    elapsed = (now - existing.last_alerted_at).total_seconds()
    if elapsed >= 600.0:  # 보수적 추정, 실제는 remind_interval_sec
        return AlertAction.REMIND

    return AlertAction.SKIP


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

    # HOLD → 기존 알림 유지 (일시적 HOLD로 인한 NEW 스팸 방지)
    # 시그널이 반대 방향으로 바뀔 때만 CHANGED 처리
    if signal_type == "HOLD":
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

    # 매매 실행 완료 → 동일 시그널이면 무시, 반대 시그널이면 해제 후 진행
    if existing.acted_at is not None:
        if existing.signal_type == signal_type:
            return AlertAction.SKIP
        # 반대 시그널 → acted_at 해제하고 CHANGED 흐름으로
        existing.acted_at = None
        logger.info(
            "alert acted cleared (signal reversed): %s %s %s→%s",
            market, timeframe, existing.signal_type, signal_type,
        )

    # 시그널 변경 (BUY→SELL, SELL→BUY) — 쿨다운 적용으로 진동 방지
    if existing.signal_type != signal_type:
        elapsed = (now - existing.last_alerted_at).total_seconds()
        old_signal = existing.signal_type
        existing.signal_type = signal_type
        existing.confidence = confidence
        existing.reasons = json.dumps(reasons, ensure_ascii=False)
        existing.indicators = indicators
        if elapsed < remind_interval_sec:
            # 쿨다운 미도래 → 상태만 업데이트, 알림은 보류
            logger.info(
                "alert change suppressed: %s %s %s→%s (%.0fs < %.0fs cooldown)",
                market, timeframe, old_signal, signal_type,
                elapsed, remind_interval_sec,
            )
            return AlertAction.SKIP
        existing.last_alerted_at = now
        logger.info(
            "alert changed: %s %s %s→%s",
            market, timeframe, old_signal, signal_type,
        )
        return AlertAction.CHANGED

    # 동일 시그널 — 패스 상태 확인 (S/R 시그널 + confidence 기반)
    if existing.passed_at is not None:
        new_fp = _pass_fingerprint(indicators, confidence)
        if not _should_clear_pass(existing.passed_reasons, new_fp):
            return AlertAction.PASSED
        # S/R 변경 또는 confidence 유의미 상승 → 패스 해제, 새 알림
        existing.passed_at = None
        existing.passed_reasons = None
        existing.confidence = confidence
        existing.reasons = json.dumps(reasons, ensure_ascii=False)
        existing.indicators = indicators
        existing.last_alerted_at = now
        logger.info(
            "alert pass cleared (sr/conf changed): %s %s %s",
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
