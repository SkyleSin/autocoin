"""테스트: 매매 실행 후 동일 시그널 재알림 방지 (acted_at)."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# src를 import path에 추가
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.alert_service import AlertAction, check_alert, check_alert_preview


def _make_session(existing_alert=None):
    """AsyncSession mock 생성."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_alert
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


def _make_alert(signal_type="BUY", acted_at=None, passed_at=None, passed_reasons=None):
    """ActiveAlert mock 생성."""
    alert = MagicMock()
    alert.signal_type = signal_type
    alert.acted_at = acted_at
    alert.passed_at = passed_at
    alert.passed_reasons = passed_reasons
    alert.last_alerted_at = datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC)
    alert.confidence = 0.7
    alert.reasons = json.dumps(["패턴: 쌍봉 (100%)"])
    alert.indicators = {}
    return alert


@pytest.mark.asyncio
async def test_acted_same_signal_skip():
    """매매 실행 후 동일 시그널 → SKIP."""
    alert = _make_alert(signal_type="BUY", acted_at=datetime.now(UTC))
    session = _make_session(alert)

    result = await check_alert(
        session, "KRW-BTC", "15m",
        signal_type="BUY", confidence=0.8,
        reasons=["test"], indicators={},
    )
    assert result == AlertAction.SKIP


@pytest.mark.asyncio
async def test_acted_opposite_signal_clears():
    """매매 실행 후 반대 시그널 → acted_at 해제."""
    alert = _make_alert(signal_type="BUY", acted_at=datetime.now(UTC))
    # last_alerted_at을 충분히 오래 전으로 설정 (쿨다운 통과)
    alert.last_alerted_at = datetime(2026, 4, 7, 0, 0, 0, tzinfo=UTC)
    session = _make_session(alert)

    result = await check_alert(
        session, "KRW-BTC", "15m",
        signal_type="SELL", confidence=0.8,
        reasons=["test"], indicators={},
    )
    assert alert.acted_at is None
    assert result == AlertAction.CHANGED


@pytest.mark.asyncio
async def test_preview_acted_same_skip():
    """preview: 매매 실행 + 동일 시그널 → SKIP."""
    alert = _make_alert(signal_type="BUY", acted_at=datetime.now(UTC))
    session = _make_session(alert)

    result = await check_alert_preview(
        session, "KRW-BTC", "15m", signal_type="BUY",
    )
    assert result == AlertAction.SKIP


@pytest.mark.asyncio
async def test_preview_no_alert_new():
    """preview: 기존 알림 없음 → NEW."""
    session = _make_session(None)

    result = await check_alert_preview(
        session, "KRW-BTC", "15m", signal_type="BUY",
    )
    assert result == AlertAction.NEW


@pytest.mark.asyncio
async def test_preview_hold_skip():
    """preview: HOLD → SKIP."""
    session = _make_session(None)

    result = await check_alert_preview(
        session, "KRW-BTC", "15m", signal_type="HOLD",
    )
    assert result == AlertAction.SKIP
