"""매매 핸들러 — check_balance, place_order, check_order."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from executor.position_sizing import calculate_position
from executor.risk_manager import check_risk
from job_runner.exceptions import JobExecutionFailed
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.workers import PrivateRequester
from models.job import Job
from models.job_status import JobStatus
from models.signal import Signal
from telegram.bot import send_signal_alert
from utils.config import Settings

logger = logging.getLogger(__name__)


def _accounts_sync(settings: Settings) -> list[dict[str, Any]]:
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/accounts")
    if not isinstance(data, list):
        raise JobExecutionFailed("accounts 응답이 리스트가 아닙니다.")
    return data


def _place_order_sync(
    settings: Settings,
    market: str,
    side: str,
    *,
    volume: str | None = None,
    price: str | None = None,
    ord_type: str = "limit",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "market": market,
        "side": side,
        "ord_type": ord_type,
    }
    if volume is not None:
        body["volume"] = volume
    if price is not None:
        body["price"] = price
    with PrivateRequester(settings) as prv:
        data = prv.request("POST", "/v1/orders", json_body=body)
    if not isinstance(data, dict):
        raise JobExecutionFailed("orders 응답이 객체가 아닙니다.")
    return data


def _get_order_sync(settings: Settings, uuid: str) -> dict[str, Any]:
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/order", params={"uuid": uuid})
    if not isinstance(data, dict):
        raise JobExecutionFailed("order 응답이 객체가 아닙니다.")
    return data


async def _chain_trade_from_balance(
    session: AsyncSession,
    settings: Settings,
    accounts: list[dict[str, Any]],
    content: dict[str, Any],
) -> None:
    """check_balance 완료 후 → 리스크 검증 → place_order 또는 거부 알림."""
    market = str(content.get("market", ""))
    side = str(content.get("side", ""))
    price = float(content.get("price", 0))
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not market or side not in ("BUY", "SELL"):
        return

    bithumb_side = "bid" if side == "BUY" else "ask"

    krw_acc = next((a for a in accounts if a.get("currency") == "KRW"), None)
    krw_balance = float(krw_acc["balance"]) if krw_acc else 0

    coin_currency = market.split("-")[1] if "-" in market else ""
    coin_acc = next(
        (a for a in accounts if a.get("currency") == coin_currency), None,
    )
    coin_balance = float(coin_acc["balance"]) if coin_acc else 0

    # 매도 시 코인 미보유 체크
    if bithumb_side == "ask" and coin_balance <= 0:
        if token and chat_id:
            await send_signal_alert(
                token, chat_id,
                f"❌ `{market}` 매도 불가 — 보유 코인 없음",
            )
        return

    # 최근 시그널에서 패턴/지표 정보 조회
    last_signal = (await session.execute(
        select(Signal)
        .where(Signal.market == market)
        .order_by(Signal.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    indicators = last_signal.indicators if last_signal else None
    confidence = last_signal.confidence if last_signal else 0.5
    patterns = None
    if indicators and isinstance(indicators.get("patterns"), dict):
        patterns = indicators["patterns"].get("patterns", [])

    # 패턴 기반 포지션 사이징
    pos = None
    if bithumb_side == "bid":
        pos = calculate_position(
            krw_balance,
            patterns=patterns,
            confidence=confidence,
            indicators=indicators,
        )
        intended = pos.amount_krw
        logger.info(
            "포지션 사이징: %s %s → %.1f%% (%s), 조절: %s",
            market, pos.pattern_name, pos.allocation_pct,
            f"{intended:,.0f}원", pos.adjustments,
        )
    else:
        intended = coin_balance * price

    risk = await check_risk(
        session, settings,
        side=bithumb_side, market=market,
        krw_balance=krw_balance,
        intended_amount_krw=intended,
    )

    if not risk.allowed:
        if token and chat_id:
            await send_signal_alert(
                token, chat_id,
                f"❌ `{market}` {side} 주문 거부\n사유: {risk.reason}",
            )
        return

    if bithumb_side == "bid":
        order_content = {
            "market": market,
            "side": "bid",
            "price": str(int(risk.max_amount_krw)),
            "ord_type": "price",
        }
    else:
        order_content = {
            "market": market,
            "side": "ask",
            "volume": str(coin_balance),
            "ord_type": "market",
        }

    order_job = Job(
        type="place_order",
        status=JobStatus.Ready.value,
        content=order_content,
        priority=1,
    )
    session.add(order_job)

    if token and chat_id:
        if bithumb_side == "bid":
            sizing_info = ""
            if pos is not None:
                adj_text = "\n".join(f"  • {a}" for a in pos.adjustments)
                sizing_info = (
                    f"\n*포지션 사이징:*\n"
                    f"  패턴: {pos.pattern_name}\n"
                    f"  비율: {pos.allocation_pct}%\n"
                    f"{adj_text}"
                )
            msg = (
                f"✅ `{market}` 매수 주문 생성\n"
                f"금액: `{risk.max_amount_krw:,.0f} KRW` (시장가)\n"
                f"KRW 잔고: `{krw_balance:,.0f}`"
                f"{sizing_info}"
            )
        else:
            msg = (
                f"✅ `{market}` 매도 주문 생성\n"
                f"수량: `{coin_balance}` {coin_currency} (시장가)\n"
                f"KRW 잔고: `{krw_balance:,.0f}`"
            )
        await send_signal_alert(token, chat_id, msg)


async def handle_check_balance(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession | None,
) -> None:
    if rate_limiter is not None:
        await rate_limiter.acquire()
    data = await asyncio.to_thread(_accounts_sync, settings)
    job.status = JobStatus.Complete.value
    job.response = {"accounts": data}

    if (
        session is not None
        and job.content
        and job.content.get("trigger") == "telegram"
    ):
        await _chain_trade_from_balance(session, settings, data, job.content)


async def handle_place_order(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
) -> None:
    if not job.content or not str(job.content.get("market", "")).strip():
        raise ValueError("place_order 잡에 content.market 이 필요합니다.")
    market = str(job.content["market"]).strip()
    side = str(job.content.get("side", "")).strip()
    if side not in ("bid", "ask"):
        raise ValueError("place_order: side는 'bid'(매수) 또는 'ask'(매도)")
    volume = job.content.get("volume")
    price = job.content.get("price")
    ord_type = str(job.content.get("ord_type", "limit"))

    if rate_limiter is not None:
        await rate_limiter.acquire()
    data = await asyncio.to_thread(
        _place_order_sync, settings, market, side,
        volume=str(volume) if volume else None,
        price=str(price) if price else None,
        ord_type=ord_type,
    )
    job.status = JobStatus.Complete.value
    job.response = {"order": data}

    if settings.telegram_bot_token and settings.telegram_chat_id:
        side_ko = "매수" if side == "bid" else "매도"
        order_uuid = data.get("uuid", "?")
        order_state = data.get("state", "?")
        await send_signal_alert(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            f"📝 `{market}` {side_ko} 주문 완료\n"
            f"주문 ID: `{order_uuid}`\n"
            f"상태: `{order_state}`",
        )


async def handle_check_order(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
) -> None:
    if not job.content or not str(job.content.get("uuid", "")).strip():
        raise ValueError("check_order 잡에 content.uuid 가 필요합니다.")
    order_uuid = str(job.content["uuid"]).strip()
    if rate_limiter is not None:
        await rate_limiter.acquire()
    data = await asyncio.to_thread(_get_order_sync, settings, order_uuid)
    job.status = JobStatus.Complete.value
    job.response = {"order": data}
