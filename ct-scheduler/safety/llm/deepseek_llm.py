"""DeepSeek API 클라이언트 — OpenAI 호환 REST API."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMDecision:
    """LLM 판단 결과."""

    decision: str   # "BUY" | "SELL" | "HOLD"
    confidence: float
    reason: str
    raw_response: str


def _extract_json(text: str) -> dict[str, Any] | None:
    """LLM 응답에서 JSON 추출. 마크다운 코드블록도 처리."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[^{}]*\"decision\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


async def query_deepseek(
    prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-reasoner",
    timeout: float = 120.0,
) -> LLMDecision:
    """DeepSeek API 호출 → LLMDecision 반환."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()

    data = resp.json()
    raw = data["choices"][0]["message"]["content"]
    logger.debug("deepseek raw response: %s", raw[:500])

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("DeepSeek JSON 파싱 실패, HOLD 반환: %s", raw[:200])
        return LLMDecision(
            decision="HOLD",
            confidence=0.0,
            reason="JSON 파싱 실패",
            raw_response=raw,
        )

    decision = str(parsed.get("decision", "HOLD")).upper().strip()
    if decision not in ("BUY", "SELL", "HOLD"):
        decision = "HOLD"

    confidence = float(parsed.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))

    reason = str(parsed.get("reason", ""))

    return LLMDecision(
        decision=decision,
        confidence=confidence,
        reason=reason,
        raw_response=raw,
    )
