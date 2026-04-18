"""Ollama 로컬 LLM 클라이언트."""

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
    # ```json ... ``` 블록 추출
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 순수 JSON 추출
    m = re.search(r"\{[^{}]*\"decision\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


async def query_ollama(
    prompt: str,
    *,
    base_url: str = "http://localhost:11434",
    model: str = "gemma3:4b",
    timeout: float = 60.0,
    num_gpu: int = 0,
) -> LLMDecision:
    """Ollama API 호출 → LLMDecision 반환."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_gpu": num_gpu},
            },
        )
        resp.raise_for_status()

    data = resp.json()
    raw = data.get("response", "")
    logger.debug("ollama raw response: %s", raw[:500])

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("LLM JSON 파싱 실패, HOLD 반환: %s", raw[:200])
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
