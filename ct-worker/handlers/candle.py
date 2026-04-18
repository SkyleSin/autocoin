"""collect_candles 핸들러 — 빗썸 캔들 조회 → ct-api 저장."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bithumb.candle import BithumbCandle
from infra.api.candle import upsert_candles
from utils.config import Settings

logger = logging.getLogger(__name__)

# timeframe → (kind, unit)
_TF_MAP: dict[str, tuple[str, int]] = {
    "15m": ("minutes", 15),
    "1h":  ("minutes", 60),
    "4h":  ("minutes", 240),
    "1d":  ("days", 0),
}


def _fetch_sync(market: str, timeframe: str, settings: Settings) -> list[dict[str, Any]]:
    """동기 빗썸 API 호출 — asyncio.to_thread()로 실행."""
    tf = _TF_MAP.get(timeframe)
    if tf is None:
        logger.warning("지원하지 않는 timeframe: %s", timeframe)
        return []
    kind, unit = tf
    client = BithumbCandle(settings)
    try:
        if kind == "minutes":
            return client.get_minutes(unit, market, count=200)
        return client.get_days(market, count=200)
    finally:
        client.close()


def _to_item(raw: dict[str, Any], market: str, timeframe: str) -> dict | None:
    """빗썸 응답 → ct-api upsert 포맷."""
    try:
        open_at = raw["candle_date_time_utc"]
        if not open_at.endswith("Z"):
            open_at += "Z"
        return {
            "market": market,
            "timeframe": timeframe,
            "open_at": open_at,
            "open": str(raw["opening_price"]),
            "high": str(raw["high_price"]),
            "low": str(raw["low_price"]),
            "close": str(raw["trade_price"]),
            "volume": str(raw["candle_acc_trade_volume"]),
        }
    except KeyError as e:
        logger.warning("캔들 필드 누락: %s — %s", e, raw)
        return None


async def handle_collect_candles(payload: dict, settings: Settings) -> None:
    market = payload.get("market")
    timeframe = payload.get("timeframe")
    if not market or not timeframe:
        logger.warning("collect_candles: payload 누락 market=%s timeframe=%s", market, timeframe)
        return

    raw = await asyncio.to_thread(_fetch_sync, market, timeframe, settings)
    if not raw:
        logger.debug("collect_candles: 결과 없음 %s %s", market, timeframe)
        return

    candles = [item for r in raw if (item := _to_item(r, market, timeframe)) is not None]
    if not candles:
        return

    await upsert_candles(candles)
    logger.info("collect_candles: %s %s → %d봉 저장", market, timeframe, len(candles))
