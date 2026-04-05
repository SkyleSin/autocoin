"""Ollama 모델 속도 비교 — 트레이딩 판단 프롬프트 기준."""

from __future__ import annotations

import time

import httpx

OLLAMA_URL = "http://localhost:11434/api/generate"

PROMPT = """당신은 암호화폐 트레이딩 전문가입니다.
아래 기술 지표를 분석하여 매매 판단을 내려주세요.

마켓: KRW-BTC
현재가: 101,800,000 KRW

기술 지표:
- RSI(14): 51.8 (중립)
- MACD: 골든크로스 (MACD > Signal)
- MACD 히스토그램: +3446 (양수, 상승 모멘텀)
- 볼린저밴드: 중간 부근 (상단 101,842K / 중간 101,804K / 하단 101,766K)
- SMA 20: 101,804K < SMA 50: 101,840K (역배열)

반드시 아래 JSON 형식으로만 응답하세요:
{"decision": "BUY 또는 SELL 또는 HOLD", "confidence": 0.0~1.0, "reason": "판단 근거 1줄"}"""

MODELS = ["gemma3:4b", "qwen2.5:7b"]


def run_inference(model: str) -> tuple[float, str]:
    start = time.perf_counter()
    resp = httpx.post(
        OLLAMA_URL,
        json={
            "model": model,
            "prompt": PROMPT,
            "stream": False,
            "options": {"num_gpu": 0},
        },
        timeout=180.0,
    )
    elapsed = time.perf_counter() - start
    data = resp.json()
    return elapsed, data.get("response", "")


def main() -> None:
    for model in MODELS:
        print(f"[워밍업] {model} 로딩 중...")
        run_inference(model)

    print("\n" + "=" * 60)
    print("속도 비교 (워밍업 후 2회 평균, CPU only)")
    print("=" * 60)

    for model in MODELS:
        times = []
        response = ""
        for i in range(2):
            elapsed, response = run_inference(model)
            times.append(elapsed)
            print(f"  {model} run {i + 1}: {elapsed:.2f}s")

        avg = sum(times) / len(times)
        print(f"\n  [{model}] 평균: {avg:.2f}s")
        print(f"  응답:\n  {response[:400]}")
        print("-" * 60)


if __name__ == "__main__":
    main()
