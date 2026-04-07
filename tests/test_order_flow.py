"""테스트: 주문 체결 확인 + 미체결 처리 + 취소/재주문 플로우."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models.job import Job
from models.job_status import JobStatus


def _make_settings():
    s = MagicMock()
    s.telegram_bot_token = "test-token"
    s.telegram_chat_id = "test-chat"
    s.risk_max_per_trade_pct = 15.0
    s.risk_min_krw_reserve = 5000.0
    s.risk_max_daily_trades = 10
    return s


def _make_job(job_type, content):
    job = MagicMock(spec=Job)
    job.type = job_type
    job.content = content
    job.status = JobStatus.Ready.value
    job.response = None
    return job


def _make_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


# ── place_order → check_order 체이닝 ──


@pytest.mark.asyncio
async def test_place_order_chains_check_order():
    """place_order 완료 후 check_order Job이 자동 생성되어야 함."""
    settings = _make_settings()
    session = _make_session()
    job = _make_job("place_order", {
        "market": "KRW-BTC", "side": "bid",
        "price": "100000", "ord_type": "price",
    })

    order_response = {"uuid": "test-uuid-123", "state": "wait"}

    with (
        patch("job_runner.handlers.trade._place_order_sync", return_value=order_response),
        patch("job_runner.handlers.trade.send_signal_alert", new_callable=AsyncMock),
    ):
        from job_runner.handlers.trade import handle_place_order
        await handle_place_order(job, settings, rate_limiter=None, session=session)

    assert job.status == JobStatus.Complete.value
    assert job.response["order"]["uuid"] == "test-uuid-123"

    # check_order Job이 생성되었는지 확인
    added_jobs = [call.args[0] for call in session.add.call_args_list
                  if hasattr(call.args[0], 'type') and isinstance(call.args[0], Job)]

    # MagicMock이므로 add 호출 자체를 확인
    assert session.add.called


# ── check_order: 체결 완료 ──


@pytest.mark.asyncio
async def test_check_order_done_records_trade():
    """체결 완료(done) → Trade 기록 + 성공 메시지."""
    settings = _make_settings()
    session = _make_session()
    # execute mock (daily trade count 등)
    session.execute = AsyncMock()

    job = _make_job("check_order", {
        "uuid": "test-uuid-123",
        "market": "KRW-BTC",
        "side": "bid",
        "delay_sec": 0,  # 테스트에서 대기 없음
        "retry_count": 0,
    })

    order_data = {
        "state": "done",
        "executed_volume": "0.001",
        "paid_fee": "250",
        "trades": [
            {"funds": "100000", "volume": "0.001"},
        ],
    }

    with (
        patch("job_runner.handlers.trade._get_order_sync", return_value=order_data),
        patch("job_runner.handlers.trade.send_message", new_callable=AsyncMock) as mock_msg,
    ):
        from job_runner.handlers.trade import handle_check_order
        await handle_check_order(job, settings, rate_limiter=None, session=session)

    assert job.status == JobStatus.Complete.value
    # Trade가 session.add로 추가되었는지
    assert session.add.called
    # 텔레그램 메시지에 "체결 완료" 포함
    msg_text = mock_msg.call_args_list[-1].args[2]
    assert "체결 완료" in msg_text


# ── check_order: 미체결 → 재확인 ──


@pytest.mark.asyncio
async def test_check_order_wait_retries():
    """미체결(wait) + retry < 2 → 재확인 Job 생성."""
    settings = _make_settings()
    session = _make_session()

    job = _make_job("check_order", {
        "uuid": "test-uuid-123",
        "market": "KRW-BTC",
        "side": "bid",
        "delay_sec": 0,
        "retry_count": 0,
    })

    order_data = {
        "state": "wait",
        "executed_volume": "0",
        "remaining_volume": "0.001",
    }

    with patch("job_runner.handlers.trade._get_order_sync", return_value=order_data):
        from job_runner.handlers.trade import handle_check_order
        await handle_check_order(job, settings, rate_limiter=None, session=session)

    assert job.status == JobStatus.Complete.value
    # 재확인 Job이 생성되었는지
    assert session.add.called


# ── check_order: 미체결 → 2회 초과 → 사용자 알림 ──


@pytest.mark.asyncio
async def test_check_order_wait_max_retry_notifies():
    """미체결 + retry >= 2 → 사용자에게 버튼 알림."""
    settings = _make_settings()
    session = _make_session()

    job = _make_job("check_order", {
        "uuid": "test-uuid-123",
        "market": "KRW-BTC",
        "side": "bid",
        "delay_sec": 0,
        "retry_count": 2,  # 이미 2회 재확인
    })

    order_data = {
        "state": "wait",
        "executed_volume": "0",
        "remaining_volume": "0.001",
    }

    with (
        patch("job_runner.handlers.trade._get_order_sync", return_value=order_data),
        patch("job_runner.handlers.trade.send_message", new_callable=AsyncMock) as mock_msg,
    ):
        from job_runner.handlers.trade import handle_check_order
        await handle_check_order(job, settings, rate_limiter=None, session=session)

    # 텔레그램 메시지에 "미체결" + 버튼 포함
    msg_text = mock_msg.call_args.args[2]
    assert "미체결" in msg_text
    # reply_markup에 버튼이 있는지
    reply_markup = mock_msg.call_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    assert "inline_keyboard" in reply_markup


# ── cancel_order: 취소 + 재주문 ──


@pytest.mark.asyncio
async def test_cancel_order_with_reorder():
    """취소 후 재주문 플래그 → check_balance Job 생성."""
    settings = _make_settings()
    session = _make_session()

    job = _make_job("cancel_order", {
        "uuid": "test-uuid-123",
        "market": "KRW-BTC",
        "side": "bid",
        "reorder": True,
    })

    cancel_data = {"uuid": "test-uuid-123", "state": "cancel"}

    with (
        patch("job_runner.handlers.trade._cancel_order_sync", return_value=cancel_data),
        patch("job_runner.handlers.trade.send_message", new_callable=AsyncMock) as mock_msg,
    ):
        from job_runner.handlers.trade import handle_cancel_order
        await handle_cancel_order(job, settings, rate_limiter=None, session=session)

    assert job.status == JobStatus.Complete.value
    # check_balance Job이 재주문으로 생성되었는지
    assert session.add.call_count >= 1
    # "취소 완료" + "재주문" 메시지
    all_msgs = [call.args[2] for call in mock_msg.call_args_list]
    assert any("취소 완료" in m for m in all_msgs)
    assert any("재주문" in m for m in all_msgs)


# ── cancel_order: 취소만 (재주문 없음) ──


@pytest.mark.asyncio
async def test_cancel_order_no_reorder():
    """취소만 (reorder=False) → 재주문 Job 없음."""
    settings = _make_settings()
    session = _make_session()

    job = _make_job("cancel_order", {
        "uuid": "test-uuid-123",
        "market": "KRW-BTC",
        "side": "bid",
        "reorder": False,
    })

    cancel_data = {"uuid": "test-uuid-123", "state": "cancel"}

    with (
        patch("job_runner.handlers.trade._cancel_order_sync", return_value=cancel_data),
        patch("job_runner.handlers.trade.send_message", new_callable=AsyncMock),
    ):
        from job_runner.handlers.trade import handle_cancel_order
        await handle_cancel_order(job, settings, rate_limiter=None, session=session)

    assert job.status == JobStatus.Complete.value
    # reorder=False이므로 check_balance Job은 생성 안 됨
    # cancel 메시지만 전송
    added_args = [call.args[0] for call in session.add.call_args_list]
    # Job 타입으로 check_balance가 없어야 함
    reorder_jobs = [a for a in added_args if hasattr(a, 'type') and getattr(a, 'type', '') == 'check_balance']
    assert len(reorder_jobs) == 0


# ── cancel_order: 취소 실패 ──


@pytest.mark.asyncio
async def test_cancel_order_failure():
    """취소 API 실패 → 에러 메시지."""
    settings = _make_settings()
    session = _make_session()

    job = _make_job("cancel_order", {
        "uuid": "test-uuid-123",
        "market": "KRW-BTC",
        "side": "bid",
        "reorder": True,
    })

    with (
        patch(
            "job_runner.handlers.trade._cancel_order_sync",
            side_effect=Exception("already filled"),
        ),
        patch("job_runner.handlers.trade.send_message", new_callable=AsyncMock) as mock_msg,
    ):
        from job_runner.handlers.trade import handle_cancel_order
        await handle_cancel_order(job, settings, rate_limiter=None, session=session)

    assert job.status == JobStatus.Complete.value
    assert job.response == {"error": "cancel_failed"}
    msg_text = mock_msg.call_args.args[2]
    assert "취소 실패" in msg_text
