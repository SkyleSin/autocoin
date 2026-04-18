"""리포트 핸들러 — generate_report."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from models.job import Job
from models.job_status import JobStatus
from utils.config import Settings

logger = logging.getLogger(__name__)


def _fetch_balances_sync(
    settings: Settings,
) -> tuple[list[dict], dict[str, float]]:
    """빗썸 잔고 + 현재가 동기 조회."""
    from bithumb.account import BithumbAccount
    from bithumb.ticker import BithumbTicker

    accounts = BithumbAccount(settings).get_balances()

    ticker_prices: dict[str, float] = {}
    coin_markets = [
        f"KRW-{a['currency']}"
        for a in accounts
        if a.get("currency") != "KRW" and float(a.get("balance", 0)) > 0
    ]
    if coin_markets:
        try:
            tickers = BithumbTicker(settings).get(",".join(coin_markets))
            for t in tickers:
                m = t.get("market", "")
                p = t.get("trade_price") or t.get("closing_price")
                if m and p:
                    ticker_prices[m] = float(p)
        except Exception:
            logger.warning("리포트: 현재가 조회 실패", exc_info=True)

    return accounts, ticker_prices


async def handle_generate_report(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession,
) -> None:
    """잔고/현재가 조회 → 리포트 생성 → 텔레그램 전송."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not (token and chat_id):
        job.response = {"status": "skip", "reason": "telegram not configured"}
        job.status = JobStatus.Complete.value
        return

    # 레이트 리밋 적용된 API 호출
    if rate_limiter is not None:
        await rate_limiter.acquire()

    try:
        accounts, ticker_prices = await asyncio.to_thread(
            _fetch_balances_sync, settings,
        )
    except Exception:
        logger.warning("리포트: 잔고/현재가 조회 실패", exc_info=True)
        accounts, ticker_prices = [], {}

    from services.report_service import generate_report

    report_text = await generate_report(
        session, accounts=accounts, ticker_prices=ticker_prices,
    )

    from infra.redis_pubsub import publish_telegram

    await publish_telegram(chat_id, report_text)

    job.response = {"status": "ok"}
    job.status = JobStatus.Complete.value
    logger.info("generate_report: 리포트 전송 완료")
