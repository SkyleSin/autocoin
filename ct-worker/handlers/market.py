"""마켓/현재가 조회 핸들러 — 빗썸 공개 API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bithumb.market import BithumbMarket
from bithumb.ticker import BithumbTicker
from infra.redis_pubsub import publish_to_telegram
from utils.config import Settings

logger = logging.getLogger(__name__)


def _fetch_markets_sync(settings: Settings) -> list[dict[str, Any]]:
    client = BithumbMarket(settings)
    try:
        return client.get_all()
    finally:
        client.close()


def _fetch_ticker_sync(settings: Settings, markets: str) -> list[dict[str, Any]]:
    client = BithumbTicker(settings)
    try:
        return client.get(markets)
    finally:
        client.close()


async def handle_get_markets(payload: dict, settings: Settings) -> None:
    """get_markets Job — KRW 마켓 목록 조회 → 텔레그램 응답.

    payload:
      - chat_id (str): 텔레그램 응답 대상
      - keyword (str, optional): 검색 키워드
    """
    chat_id = payload.get("chat_id")
    keyword = payload.get("keyword", "").upper()

    markets = await asyncio.to_thread(_fetch_markets_sync, settings)
    krw = [m for m in markets if str(m.get("market", "")).startswith("KRW-")]

    if keyword:
        krw = [
            m for m in krw
            if m.get("market", "").replace("KRW-", "").startswith(keyword)
            or keyword in m.get("english_name", "").upper()
            or keyword in m.get("korean_name", "")
        ]
    krw.sort(key=lambda m: m["market"])

    if not krw:
        text = f"검색 결과 없음: `{keyword}`" if keyword else "마켓 없음"
    else:
        header = f"📋 *빗썸 KRW 마켓* ({len(krw)}개)"
        if keyword:
            header = f"📋 *빗썸 KRW 마켓* (검색: {keyword}, {len(krw)}개)"
        lines = [header + "\n"]
        for m in krw[:70]:
            lines.append(
                f"`{m['market']}` — {m.get('korean_name', '')} "
                f"({m.get('english_name', '')})",
            )
        if len(krw) > 70:
            lines.append(f"\n... 외 {len(krw) - 70}개")
        text = "\n".join(lines)

    if chat_id:
        await publish_to_telegram({
            "type": "text",
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })

    logger.info("get_markets: %d개 조회 (keyword=%s)", len(krw), keyword or "전체")


async def handle_get_ticker(payload: dict, settings: Settings) -> None:
    """get_ticker Job — 현재가 조회.

    payload:
      - markets (str): 콤마 구분 마켓 코드 (예: "KRW-BTC,KRW-ETH")
      - chat_id (str, optional): 텔레그램 응답 대상
    """
    markets_str = payload.get("markets", "")
    chat_id = payload.get("chat_id")

    if not markets_str:
        logger.warning("get_ticker: markets 누락")
        return

    tickers = await asyncio.to_thread(_fetch_ticker_sync, settings, markets_str)

    if chat_id:
        lines = ["📈 *현재가*\n"]
        for t in tickers:
            market = t.get("market", "?")
            price = float(t.get("trade_price", 0))
            change = t.get("change", "")
            change_rate = float(t.get("signed_change_rate", 0)) * 100
            emoji = "🔴" if change == "FALL" else "🟢" if change == "RISE" else "⚪"
            lines.append(f"{emoji} `{market}`: {price:,.0f} ({change_rate:+.2f}%)")
        await publish_to_telegram({
            "type": "text",
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "Markdown",
        })

    logger.info("get_ticker: %d개 조회", len(tickers))
