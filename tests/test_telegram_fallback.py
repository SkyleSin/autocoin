"""테스트: 텔레그램 Markdown 파싱 실패 → 플레인 텍스트 폴백."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.mark.asyncio
async def test_send_message_markdown_fallback():
    """Markdown 파싱 실패 시 parse_mode 없이 재전송."""
    from telegram.bot import send_message

    # 1차 응답: 400 + parse entities 에러
    resp_fail = MagicMock()
    resp_fail.is_success = False
    resp_fail.status_code = 400
    resp_fail.text = '{"description":"Bad Request: can\'t parse entities"}'

    # 2차 응답: 성공
    resp_ok = MagicMock()
    resp_ok.is_success = True

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[resp_fail, resp_ok])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("telegram.bot.httpx.AsyncClient", return_value=mock_client):
        result = await send_message("token", "chat_id", "test *broken_ markdown")

    assert result is True
    # 2번째 호출은 parse_mode 없이
    second_call_payload = mock_client.post.call_args_list[1][1]["json"]
    assert "parse_mode" not in second_call_payload


@pytest.mark.asyncio
async def test_send_message_other_error_no_retry():
    """400이지만 parse entities가 아닌 에러 → 재시도 안 함."""
    from telegram.bot import send_message

    resp_fail = MagicMock()
    resp_fail.is_success = False
    resp_fail.status_code = 400
    resp_fail.text = '{"description":"Bad Request: chat not found"}'

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=resp_fail)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("telegram.bot.httpx.AsyncClient", return_value=mock_client):
        result = await send_message("token", "chat_id", "test")

    assert result is False
    assert mock_client.post.call_count == 1  # 재시도 없음
