"""리밸런싱 제안 로직 — KRW 부족 시 약한 코인 매도 추천."""
from __future__ import annotations

import logging
from typing import Any

from infra.api.signal import get_latest_signal
from infra.redis_pubsub import publish_telegram
from utils.config import Settings

from job_runner.handlers.trade_rebalance_ui import (
    build_rebalance_buttons,
    build_rebalance_message,
)

BITHUMB_FEE_PCT = 0.25
logger = logging.getLogger(__name__)


async def suggest_rebalance(
    settings: Settings,
    accounts: list[dict[str, Any]],
    *,
    buy_market: str,
    buy_price: float,
    buy_confidence: float,
    intended_krw: float,
) -> None:
    """KRW 부족 시 약한 코인 매도 제안."""
    chat_id = settings.telegram_chat_id
    holdings = await _collect_holdings(accounts, buy_market)
    if not holdings:
        await publish_telegram(
            chat_id,
            f"❌ `{buy_market}` 매수 잔고 부족\n리밸런싱 불가",
        )
        return

    holdings.sort(
        key=lambda h: (0 if h["signal"] == "SELL" else 1, h["confidence"]),
    )
    weakest = holdings[0]

    if weakest["value_krw"] <= 0:
        await publish_telegram(
            chat_id, f"❌ `{buy_market}` 매수 잔고 부족\n시세 확인 불가",
        )
        return

    conf_diff = buy_confidence - weakest["confidence"]
    rec_pct = _recommend_pct(conf_diff)
    sell_val = weakest["value_krw"] * rec_pct / 100
    fee = sell_val * BITHUMB_FEE_PCT / 100 * 2
    lines = build_rebalance_message(
        buy_market, buy_confidence, intended_krw, weakest,
        weakest["value_krw"], rec_pct, conf_diff, sell_val, fee, sell_val - fee,
    )
    buttons = build_rebalance_buttons(weakest, buy_market, buy_price, rec_pct)
    await publish_telegram(chat_id, "\n".join(lines), reply_markup=buttons)


async def _collect_holdings(
    accounts: list[dict[str, Any]], buy_market: str,
) -> list[dict[str, Any]]:
    """보유 코인 목록 수집."""
    holdings: list[dict[str, Any]] = []
    for acc in accounts:
        cur = acc.get("currency", "")
        bal = float(acc.get("balance", 0))
        if cur == "KRW" or bal <= 0:
            continue
        mkt = f"KRW-{cur}"
        if mkt == buy_market:
            continue
        sig_data = await get_latest_signal(mkt)
        conf = float(sig_data.get("confidence", 0.5)) if sig_data else 0.5
        sig_type = sig_data.get("signal_type", "HOLD") if sig_data else "HOLD"
        px = 0.0
        if sig_data and sig_data.get("indicators"):
            ind = sig_data["indicators"]
            px = float(
                ind.get("bb_middle", 0) or ind.get("sma_20", 0) or 0
            )
        holdings.append({
            "market": mkt, "currency": cur, "balance": bal,
            "price": px, "value_krw": bal * px if px > 0 else 0,
            "confidence": conf, "signal": sig_type,
        })
    return holdings


def _recommend_pct(conf_diff: float) -> int:
    if conf_diff >= 0.4:
        return 100
    if conf_diff >= 0.25:
        return 75
    if conf_diff >= 0.1:
        return 50
    return 25
