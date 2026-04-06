"""수익률 리포트 자동 전송 스케줄러 — 오전 9시 / 오후 9시 (KST)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.report_service import generate_report
from telegram.bot import send_message
from utils.config import Settings

logger = logging.getLogger(__name__)

REPORT_HOURS_KST = [9, 21]  # 오전 9시, 오후 9시 (KST)


async def report_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """오전 9시 / 오후 9시(KST)에 리포트 자동 전송."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not (token and chat_id):
        logger.info("report_scheduler: 텔레그램 미설정, 비활성")
        return

    while True:
        try:
            now_utc = datetime.now(UTC)
            now_kst = now_utc + timedelta(hours=9)

            # 다음 리포트 시간 계산
            next_report = _next_report_time(now_kst)
            wait_sec = (next_report - now_kst).total_seconds()

            logger.info(
                "report_scheduler: 다음 리포트 %s KST (%.0f초 후)",
                next_report.strftime("%H:%M"), wait_sec,
            )
            await asyncio.sleep(wait_sec)

            # 리포트 생성 + 전송
            accounts, ticker_prices = await _fetch_balances(settings)
            async with session_factory() as session:
                report_text = await generate_report(
                    session, accounts=accounts, ticker_prices=ticker_prices,
                )
                await session.commit()

            await send_message(token, chat_id, report_text)
            logger.info("report_scheduler: 리포트 전송 완료")

            # 중복 전송 방지 (1분 대기)
            await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("report_scheduler: 종료")
            break
        except Exception:
            logger.exception("report_scheduler: 오류, 60초 후 재시도")
            await asyncio.sleep(60)


def _next_report_time(now_kst: datetime) -> datetime:
    """현재 KST 시간 기준 다음 리포트 시간 계산."""
    today = now_kst.replace(minute=0, second=0, microsecond=0)
    candidates = [today.replace(hour=h) for h in REPORT_HOURS_KST]
    # 내일 것도 포함
    tomorrow = today + timedelta(days=1)
    candidates += [tomorrow.replace(hour=h) for h in REPORT_HOURS_KST]
    # 현재보다 미래인 것 중 가장 가까운 것
    future = [c for c in candidates if c > now_kst]
    return min(future)


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
