"""테스트: DeepSeek API 실패 → Ollama 폴백."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_engine.deepseek_llm import LLMDecision, query_deepseek, _extract_json


def test_extract_json_code_block():
    """마크다운 코드블록에서 JSON 추출."""
    text = '```json\n{"decision": "BUY", "confidence": 0.8, "reason": "test"}\n```'
    result = _extract_json(text)
    assert result is not None
    assert result["decision"] == "BUY"


def test_extract_json_plain():
    """순수 JSON 추출."""
    text = 'Some text {"decision": "SELL", "confidence": 0.7, "reason": "test"} more text'
    result = _extract_json(text)
    assert result is not None
    assert result["decision"] == "SELL"


def test_extract_json_none():
    """JSON 없으면 None."""
    result = _extract_json("no json here")
    assert result is None


@pytest.mark.asyncio
async def test_deepseek_success():
    """DeepSeek 정상 응답 파싱."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": '{"decision": "HOLD", "confidence": 0.7, "reason": "추세 불명확"}'
            }
        }]
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("ai_engine.deepseek_llm.httpx.AsyncClient", return_value=mock_client):
        result = await query_deepseek("test prompt", api_key="sk-test")

    assert result.decision == "HOLD"
    assert result.confidence == 0.7
    assert "추세 불명확" in result.reason


@pytest.mark.asyncio
async def test_deepseek_parse_failure_returns_hold():
    """DeepSeek 응답이 JSON이 아니면 HOLD 반환."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{
            "message": {"content": "I cannot provide financial advice."}
        }]
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("ai_engine.deepseek_llm.httpx.AsyncClient", return_value=mock_client):
        result = await query_deepseek("test", api_key="sk-test")

    assert result.decision == "HOLD"
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_deepseek_invalid_decision_normalized():
    """유효하지 않은 decision → HOLD로 정규화."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": '{"decision": "MAYBE", "confidence": 0.5, "reason": "unsure"}'
            }
        }]
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("ai_engine.deepseek_llm.httpx.AsyncClient", return_value=mock_client):
        result = await query_deepseek("test", api_key="sk-test")

    assert result.decision == "HOLD"
