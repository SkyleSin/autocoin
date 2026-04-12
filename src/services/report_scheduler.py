"""리포트 유틸 — 잔고/현재가 조회 헬퍼.

스케줄 루프는 unified_scheduler.py에서 관리.
"""

from __future__ import annotations

import logging

from utils.config import Settings

logger = logging.getLogger(__name__)


async def _fetch_balances(settings: Settings) -> tuple[list[dict], dict[str, float]]:
    """빗썸 잔고 + 현재가 조회."""
    accounts: list[dict] = []
    ticker_prices: dict[str, float] = {}
    try:
        from bithumb.account import BithumbAccount
        from bithumb.ticker import BithumbTicker

        client = BithumbAccount(settings)
        accounts = client.get_balances()

        # 보유 코인의 현재가 조회
        ticker_client = BithumbTicker(settings)
        coin_markets = [
            f"KRW-{a['currency']}"
            for a in accounts
            if a.get("currency") != "KRW" and float(a.get("balance", 0)) > 0
        ]
        if coin_markets:
            try:
                tickers = ticker_client.get(",".join(coin_markets))
                for t in tickers:
                    m = t.get("market", "")
                    p = t.get("trade_price") or t.get("closing_price")
                    if m and p:
                        ticker_prices[m] = float(p)
            except Exception:
                pass
    except Exception:
        logger.warning("리포트: 잔고/현재가 조회 실패", exc_info=True)
    return accounts, ticker_prices
