"""Redis 1분봉 캐시 — ct-worker가 저장, ct-strategy가 조회."""

from __future__ import annotations

import json

from infra.redis_client import get_redis

_PREFIX = "candles:1m:"
_TTL = 180  # 3분


async def save_candles_1m(market: str, candles: list[dict]) -> None:
    """1분봉 캐시 저장 (TTL 3분)."""
    r = get_redis()
    await r.set(f"{_PREFIX}{market}", json.dumps(candles), ex=_TTL)


async def get_candles_1m(market: str) -> list[dict]:
    """1분봉 캐시 조회. 없으면 빈 리스트."""
    r = get_redis()
    data = await r.get(f"{_PREFIX}{market}")
    if data:
        return json.loads(data)
    return []


async def get_current_price(market: str) -> float:
    """1분봉 캐시에서 최신 현재가 추출. 없으면 0.0."""
    candles = await get_candles_1m(market)
    if candles:
        return float(candles[-1].get("trade_price", 0))
    return 0.0


async def get_current_prices(markets: list[str]) -> dict[str, float]:
    """여러 마켓의 현재가를 1분봉 캐시에서 일괄 추출."""
    result = {}
    for market in markets:
        price = await get_current_price(market)
        if price > 0:
            result[market] = price
    return result
