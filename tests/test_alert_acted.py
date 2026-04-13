"""테스트: 매매 실행 후 동일 시그널 재알림 방지 (acted_at)."""

import json

# src를 import path에 추가
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.alert_service import (
    AlertAction,
    _pass_fingerprint,
    _should_clear_pass,
    check_alert,
    check_alert_preview,
    mark_passed,
)


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


# --- 패스 해제 로직 (S/R + confidence 기반) ---


def test_fingerprint_extracts_sr_signal():
    """핑거프린트가 S/R 시그널과 confidence를 추출."""
    indicators = {"support_resistance": {"signal": "SUPPORT_BOUNCE"}}
    fp = json.loads(_pass_fingerprint(indicators, 0.65))
    assert fp["sr"] == "SUPPORT_BOUNCE"
    assert fp["conf"] == 0.65


def test_fingerprint_none_indicators():
    """indicators가 None이면 sr=NONE."""
    fp = json.loads(_pass_fingerprint(None, 0.5))
    assert fp["sr"] == "NONE"


def test_should_clear_pass_old_format():
    """기존 DB에 reasons JSON array가 남아있는 경우 → 안전하게 해제."""
    old_format = json.dumps(["패턴: 쌍봉 (100%)"])
    new = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.6)
    assert _should_clear_pass(old_format, new) is True


def test_should_clear_pass_sr_changed():
    """S/R 시그널 변경 → 패스 해제."""
    old = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.6)
    new = _pass_fingerprint({"support_resistance": {"signal": "RESISTANCE_BREAK"}}, 0.6)
    assert _should_clear_pass(old, new) is True


def test_should_clear_pass_same_sr_same_conf():
    """S/R 동일 + confidence 동일 → 패스 유지."""
    old = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.6)
    new = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.6)
    assert _should_clear_pass(old, new) is False


def test_should_clear_pass_conf_increased():
    """S/R 동일 + confidence 0.15 이상 상승 → 패스 해제."""
    old = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.5)
    new = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.65)
    assert _should_clear_pass(old, new) is True


def test_should_clear_pass_conf_small_increase():
    """S/R 동일 + confidence 소폭 상승 → 패스 유지."""
    old = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.5)
    new = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.6)
    assert _should_clear_pass(old, new) is False


def test_should_clear_pass_conf_decreased():
    """confidence 하락은 패스 해제 안 함."""
    old = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.8)
    new = _pass_fingerprint({"support_resistance": {"signal": "SUPPORT_BOUNCE"}}, 0.5)
    assert _should_clear_pass(old, new) is False


@pytest.mark.asyncio
async def test_passed_same_sr_stays_passed():
    """패스 상태 + S/R 동일 + confidence 동일 → PASSED 유지."""
    indicators = {"support_resistance": {"signal": "SUPPORT_BOUNCE"}}
    fp = _pass_fingerprint(indicators, 0.6)
    alert = _make_alert(
        signal_type="BUY",
        passed_at=datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC),
        passed_reasons=fp,
    )
    alert.indicators = indicators
    session = _make_session(alert)

    result = await check_alert(
        session, "KRW-BTC", "15m",
        signal_type="BUY", confidence=0.6,
        reasons=["test"], indicators=indicators,
    )
    assert result == AlertAction.PASSED


@pytest.mark.asyncio
async def test_passed_sr_changed_clears():
    """패스 상태 + S/R 변경 → CHANGED로 패스 해제."""
    old_indicators = {"support_resistance": {"signal": "SUPPORT_BOUNCE"}}
    new_indicators = {"support_resistance": {"signal": "RESISTANCE_BREAK"}}
    fp = _pass_fingerprint(old_indicators, 0.6)
    alert = _make_alert(
        signal_type="BUY",
        passed_at=datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC),
        passed_reasons=fp,
    )
    alert.indicators = old_indicators
    session = _make_session(alert)

    result = await check_alert(
        session, "KRW-BTC", "15m",
        signal_type="BUY", confidence=0.6,
        reasons=["test"], indicators=new_indicators,
    )
    assert result == AlertAction.CHANGED
    assert alert.passed_at is None


@pytest.mark.asyncio
async def test_passed_conf_jump_clears():
    """패스 상태 + confidence 크게 상승 → CHANGED로 패스 해제."""
    indicators = {"support_resistance": {"signal": "SUPPORT_BOUNCE"}}
    fp = _pass_fingerprint(indicators, 0.5)
    alert = _make_alert(
        signal_type="BUY",
        passed_at=datetime(2026, 4, 7, 12, 0, 0, tzinfo=UTC),
        passed_reasons=fp,
    )
    alert.indicators = indicators
    session = _make_session(alert)

    result = await check_alert(
        session, "KRW-BTC", "15m",
        signal_type="BUY", confidence=0.7,
        reasons=["test"], indicators=indicators,
    )
    assert result == AlertAction.CHANGED
    assert alert.passed_at is None


@pytest.mark.asyncio
async def test_mark_passed_saves_fingerprint():
    """mark_passed가 reasons 대신 S/R 핑거프린트를 저장."""
    alert = _make_alert(signal_type="BUY")
    alert.indicators = {"support_resistance": {"signal": "SUPPORT_BOUNCE"}}
    alert.confidence = 0.7

    results_mock = MagicMock()
    results_mock.all.return_value = [alert]
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [alert]

    session = AsyncMock()
    result_obj = MagicMock()
    result_obj.scalars.return_value = scalars_mock
    session.execute = AsyncMock(return_value=result_obj)

    await mark_passed(session, "KRW-BTC")

    saved = json.loads(alert.passed_reasons)
    assert saved["sr"] == "SUPPORT_BOUNCE"
    assert saved["conf"] == 0.7
