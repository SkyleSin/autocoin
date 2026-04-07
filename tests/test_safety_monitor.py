"""테스트: 긴급 매도 안전장치."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _make_settings():
    s = MagicMock()
    s.telegram_bot_token = "test-token"
    s.telegram_chat_id = "test-chat"
    s.deepseek_api_key = "sk-test"
    s.deepseek_base_url = "https://api.deepseek.com"
    s.deepseek_model = "deepseek-reasoner"
    s.deepseek_timeout = 120.0
    s.ollama_base_url = "http://localhost:11434"
    s.ollama_model = "gemma3:4b"
    s.ollama_timeout = 60.0
    s.ollama_num_gpu = 0
    s.safety_monitor_interval_sec = 60.0
    s.safety_breach_immediate_pct = 3.0
    s.safety_breach_consecutive_pct = 1.0
    s.safety_breach_consecutive_count = 3
    s.safety_cooldown_sec = 300.0
    return s


def _make_session(signal=None):
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = signal
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


def _make_signal(nearest_support=100000):
    sig = MagicMock()
    sig.indicators = {
        "rsi_14": 35.0,
        "macd_histogram": -50.0,
        "sma_20": 102000,
        "sma_50": 105000,
        "support_resistance": {
            "nearest_support": nearest_support,
            "nearest_resistance": 110000,
            "signal": "SUPPORT_BOUNCE",
        },
        "patterns": {"patterns": []},
    }
    return sig


# ── 지지선 추출 ──


@pytest.mark.asyncio
async def test_get_nearest_support():
    from safety.monitor import _get_nearest_support

    signal = _make_signal(100000)
    session = _make_session(signal)
    support, indicators = await _get_nearest_support(session, "KRW-BTC")
    assert support == 100000
    assert indicators is not None


@pytest.mark.asyncio
async def test_get_nearest_support_none():
    from safety.monitor import _get_nearest_support

    session = _make_session(None)
    support, indicators = await _get_nearest_support(session, "KRW-BTC")
    assert support is None


# ── 이탈 감지 로직 ──


@pytest.mark.asyncio
async def test_immediate_trigger_3pct():
    """3%+ 이탈 → 즉시 LLM 호출 + 매도."""
    from safety.monitor import _breach_counts, _check_single_asset, _last_triggered

    _breach_counts.clear()
    _last_triggered.clear()

    settings = _make_settings()
    signal = _make_signal(100000)
    session = _make_session(signal)

    # 현재가 96000 → 4% 이탈
    llm_result = MagicMock()
    llm_result.decision = "SELL"
    llm_result.confidence = 0.9
    llm_result.reason = "급락 중, 추가 하락 예상"

    with (
        patch("safety.monitor._get_ticker_sync", return_value=96000),
        patch("safety.monitor._call_emergency_llm", new_callable=AsyncMock, return_value=llm_result),
        patch("safety.monitor.send_message", new_callable=AsyncMock) as mock_msg,
    ):
        await _check_single_asset(session, settings, "BTC", 0.5)

    # place_order Job이 생성되었는지
    assert session.add.called
    # 텔레그램에 "긴급 매도 실행" 메시지
    msg_text = mock_msg.call_args.args[2]
    assert "긴급 매도 실행" in msg_text


@pytest.mark.asyncio
async def test_immediate_trigger_hold():
    """3%+ 이탈이지만 LLM이 HOLD → 경고만."""
    from safety.monitor import _breach_counts, _check_single_asset, _last_triggered

    _breach_counts.clear()
    _last_triggered.clear()

    settings = _make_settings()
    signal = _make_signal(100000)
    session = _make_session(signal)

    llm_result = MagicMock()
    llm_result.decision = "HOLD"
    llm_result.confidence = 0.6
    llm_result.reason = "RSI 과매도, 반등 가능"

    with (
        patch("safety.monitor._get_ticker_sync", return_value=96000),
        patch("safety.monitor._call_emergency_llm", new_callable=AsyncMock, return_value=llm_result),
        patch("safety.monitor.send_message", new_callable=AsyncMock) as mock_msg,
    ):
        await _check_single_asset(session, settings, "BTC", 0.5)

    # place_order는 안 생김
    added = [c.args[0] for c in session.add.call_args_list]
    order_jobs = [a for a in added if hasattr(a, "type") and getattr(a, "type", "") == "place_order"]
    assert len(order_jobs) == 0
    # 경고 메시지
    msg_text = mock_msg.call_args.args[2]
    assert "이탈 감지" in msg_text


@pytest.mark.asyncio
async def test_consecutive_trigger():
    """1~3% 이탈 3회 연속 → LLM 호출."""
    from safety.monitor import _breach_counts, _check_single_asset, _last_triggered

    _breach_counts.clear()
    _last_triggered.clear()

    settings = _make_settings()
    signal = _make_signal(100000)
    session = _make_session(signal)

    llm_result = MagicMock()
    llm_result.decision = "SELL"
    llm_result.confidence = 0.8
    llm_result.reason = "지속 하락"

    # 현재가 98500 → 1.5% 이탈
    with (
        patch("safety.monitor._get_ticker_sync", return_value=98500),
        patch("safety.monitor._call_emergency_llm", new_callable=AsyncMock, return_value=llm_result) as mock_llm,
        patch("safety.monitor.send_message", new_callable=AsyncMock),
    ):
        # 1회차 — 감시만
        await _check_single_asset(session, settings, "BTC", 0.5)
        assert not mock_llm.called

        # 2회차 — 감시만
        await _check_single_asset(session, settings, "BTC", 0.5)
        assert not mock_llm.called

        # 3회차 — 트리거!
        await _check_single_asset(session, settings, "BTC", 0.5)
        assert mock_llm.called


@pytest.mark.asyncio
async def test_below_1pct_ignored():
    """1% 미만 이탈 → 무시."""
    from safety.monitor import _breach_counts, _check_single_asset, _last_triggered

    _breach_counts.clear()
    _last_triggered.clear()

    settings = _make_settings()
    signal = _make_signal(100000)
    session = _make_session(signal)

    # 현재가 99500 → 0.5% 이탈
    with (
        patch("safety.monitor._get_ticker_sync", return_value=99500),
        patch("safety.monitor._call_emergency_llm", new_callable=AsyncMock) as mock_llm,
        patch("safety.monitor.send_message", new_callable=AsyncMock),
    ):
        await _check_single_asset(session, settings, "BTC", 0.5)
        assert not mock_llm.called


@pytest.mark.asyncio
async def test_cooldown_prevents_retrigger():
    """쿨다운 내 재트리거 방지."""
    from datetime import UTC, datetime

    from safety.monitor import _breach_counts, _check_single_asset, _last_triggered

    _breach_counts.clear()
    _last_triggered.clear()
    _last_triggered["KRW-BTC"] = datetime.now(UTC)  # 방금 트리거됨

    settings = _make_settings()
    signal = _make_signal(100000)
    session = _make_session(signal)

    with (
        patch("safety.monitor._get_ticker_sync", return_value=96000),
        patch("safety.monitor._call_emergency_llm", new_callable=AsyncMock) as mock_llm,
        patch("safety.monitor.send_message", new_callable=AsyncMock),
    ):
        await _check_single_asset(session, settings, "BTC", 0.5)
        assert not mock_llm.called  # 쿨다운으로 스킵


@pytest.mark.asyncio
async def test_breach_counter_resets_on_recovery():
    """가격 복귀 시 카운터 리셋."""
    from safety.monitor import _breach_counts, _check_single_asset, _last_triggered

    _breach_counts.clear()
    _last_triggered.clear()

    settings = _make_settings()
    signal = _make_signal(100000)
    session = _make_session(signal)

    with (
        patch("safety.monitor._call_emergency_llm", new_callable=AsyncMock),
        patch("safety.monitor.send_message", new_callable=AsyncMock),
    ):
        # 2회 이탈
        with patch("safety.monitor._get_ticker_sync", return_value=98500):
            await _check_single_asset(session, settings, "BTC", 0.5)
            await _check_single_asset(session, settings, "BTC", 0.5)
        assert _breach_counts.get("KRW-BTC", 0) == 2

        # 가격 복귀
        with patch("safety.monitor._get_ticker_sync", return_value=100500):
            await _check_single_asset(session, settings, "BTC", 0.5)
        assert _breach_counts.get("KRW-BTC", 0) == 0


# ── 프롬프트 ──


def test_emergency_prompt_format():
    from ai_engine.emergency_prompt import build_emergency_prompt

    prompt = build_emergency_prompt(
        market="KRW-BTC",
        current_price=96000000,
        support_level=100000000,
        breach_pct=4.0,
        consecutive_minutes=1,
        indicators={"rsi_14": 22.5, "macd_histogram": -500.0},
    )
    assert "KRW-BTC" in prompt
    assert "96,000,000" in prompt
    assert "100,000,000" in prompt
    assert "4.0%" in prompt
    assert "SELL/HOLD" in prompt
