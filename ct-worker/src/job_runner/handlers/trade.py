"""매매 핸들러 — check_balance, place_order, check_order."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.strategy.executor.position_sizing import calculate_position
from core.strategy.executor.risk_manager import check_risk
from infra.redis_pubsub import publish_telegram
from models.job import Job
from models.job_status import JobStatus
from models.signal import Signal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.config import Settings

from job_runner.exceptions import JobExecutionFailed
from job_runner.rate_limit import AsyncSlidingWindowRateLimiter
from job_runner.workers import PrivateRequester

# 빗썸 거래 수수료 (편도)
BITHUMB_FEE_PCT = 0.25

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
    params: dict[str, Any] = {
        "market": market,
        "side": side,
        "ord_type": ord_type,
    }
    if volume is not None:
        params["volume"] = volume
    if price is not None:
        params["price"] = price
    with PrivateRequester(settings) as prv:
        data = prv.request("POST", "/v1/orders", json_body=params)
    if not isinstance(data, dict):
        raise JobExecutionFailed("orders 응답이 객체가 아닙니다.")
    return data


def _get_order_sync(settings: Settings, uuid: str) -> dict[str, Any]:
    with PrivateRequester(settings) as prv:
        data = prv.request("GET", "/v1/order", params={"uuid": uuid})
    if not isinstance(data, dict):
        raise JobExecutionFailed("order 응답이 객체가 아닙니다.")
    return data


def _cancel_order_sync(settings: Settings, uuid: str) -> dict[str, Any]:
    with PrivateRequester(settings) as prv:
        data = prv.request("DELETE", "/v1/order", json_body={"uuid": uuid})
    if not isinstance(data, dict):
        raise JobExecutionFailed("cancel 응답이 객체가 아닙니다.")
    return data


async def _suggest_rebalance(
    session: AsyncSession,
    settings: Settings,
    accounts: list[dict[str, Any]],
    *,
    buy_market: str,
    buy_price: float,
    buy_confidence: float,
    intended_krw: float,
) -> None:
    """KRW 부족 시 리밸런싱 제안. 로직은 trade_rebalance로 위임."""
    from job_runner.handlers.trade_rebalance import suggest_rebalance
    await suggest_rebalance(
        settings, accounts,
        buy_market=buy_market, buy_price=buy_price,
        buy_confidence=buy_confidence, intended_krw=intended_krw,
    )


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
            await publish_telegram(chat_id, f"❌ `{market}` 매도 불가 — 보유 코인 없음")
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

    # 코인당 포지션 한도 체크 (매수 시)
    if bithumb_side == "bid" and settings.risk_max_per_coin_pct > 0:
        total_capital = krw_balance + sum(
            float(a.get("balance", 0)) * float(a.get("avg_buy_price", 0))
            for a in accounts
            if a.get("currency") not in ("KRW", "") and float(a.get("balance", 0)) > 0
        )
        coin_value = coin_balance * price if coin_balance > 0 and price > 0 else 0
        coin_pct = coin_value / total_capital * 100 if total_capital > 0 else 0
        if total_capital > 0 and coin_pct >= settings.risk_max_per_coin_pct:
            logger.info(
                "코인당 한도 초과: %s %.1f%% >= %.1f%%",
                market, coin_pct, settings.risk_max_per_coin_pct,
            )
            if token and chat_id:
                await publish_telegram(
                    chat_id,
                    f"⚠️ `{market}` 매수 차단 — 코인당 한도 "
                    f"({coin_pct:.1f}% >= "
                    f"{settings.risk_max_per_coin_pct:.0f}%)",
                )
            return

    # 패턴 기반 포지션 사이징
    pos = None
    if bithumb_side == "bid":
        pos = calculate_position(
            krw_balance,
            patterns=patterns,
            confidence=confidence,
            indicators=indicators,
            max_per_trade_pct=settings.risk_max_per_trade_pct,
        )
        intended = pos.amount_krw
        logger.info(
            "포지션 사이징: %s %s → %.1f%% (%s), 조절: %s",
            market, pos.pattern_name, pos.allocation_pct,
            f"{intended:,.0f}원", pos.adjustments,
        )
    else:
        intended = coin_balance * price

    # 일일 매매 횟수 조회 (ct-scheduler DB 경로)
    from datetime import UTC, datetime

    from models.trade import Trade
    from sqlalchemy import func as sa_func
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    daily_count = (await session.execute(
        select(sa_func.count(Trade.id)).where(
            Trade.created_at >= today_start,
        )
    )).scalar_one()

    risk = await check_risk(
        settings,
        side=bithumb_side, market=market,
        krw_balance=krw_balance,
        intended_amount_krw=intended,
        daily_trade_count=daily_count,
    )

    if not risk.allowed:
        # 잔고 부족으로 거부된 매수 → 리밸런싱 기회 탐색
        if bithumb_side == "bid" and "잔고 부족" in risk.reason and token and chat_id:
            await _suggest_rebalance(
                session, settings, accounts,
                buy_market=market, buy_price=price,
                buy_confidence=confidence,
                intended_krw=intended,
            )
        elif token and chat_id:
            await publish_telegram(
                chat_id,
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
        sell_pct = int(content.get("sell_pct", 100))
        sell_volume = coin_balance * (sell_pct / 100)
        order_content = {
            "market": market,
            "side": "ask",
            "volume": str(sell_volume),
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
            sell_pct = int(content.get("sell_pct", 100))
            msg = (
                f"✅ `{market}` 매도 주문 생성\n"
                f"비율: `{sell_pct}%` → 수량: `{sell_volume}` {coin_currency}\n"
                f"보유: `{coin_balance}` {coin_currency}\n"
                f"KRW 잔고: `{krw_balance:,.0f}`"
            )
        await publish_telegram(chat_id, msg)

    # 리밸런싱: 매도 완료 후 매수 체이닝
    if (
        bithumb_side == "ask"
        and content.get("trigger") == "rebalance"
        and content.get("rebalance_buy_market")
    ):
        buy_market = str(content["rebalance_buy_market"])
        buy_price = float(content.get("rebalance_buy_price", 0))
        rebalance_buy_job = Job(
            type="check_balance",
            status=JobStatus.Ready.value,
            content={
                "trigger": "telegram",
                "market": buy_market,
                "side": "BUY",
                "price": buy_price,
            },
            priority=4,
        )
        session.add(rebalance_buy_job)
        if token and chat_id:
            await publish_telegram(
                chat_id,
                f"🔄 리밸런싱 2단계: `{buy_market}` 매수 진행 중...",
            )


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
        and job.content.get("trigger") in ("telegram", "rebalance")
    ):
        await _chain_trade_from_balance(session, settings, data, job.content)


async def handle_place_order(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession | None = None,
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

    order_uuid = data.get("uuid", "")
    order_state = data.get("state", "")

    if settings.telegram_chat_id:
        side_ko = "매수" if side == "bid" else "매도"
        await publish_telegram(
            settings.telegram_chat_id,
            f"📝 `{market}` {side_ko} 주문 접수\n"
            f"주문 ID: `{order_uuid}`\n"
            f"상태: `{order_state}`\n"
            f"30초 후 체결 확인 예정",
        )

    # 30초 후 체결 확인 Job 자동 생성
    if session is not None and order_uuid:
        check_job = Job(
            type="check_order",
            status=JobStatus.Ready.value,
            content={
                "uuid": order_uuid,
                "market": market,
                "side": side,
                "ord_type": ord_type,
                "delay_sec": 30,
                "retry_count": 0,
            },
            priority=2,
        )
        session.add(check_job)


async def handle_check_order(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession | None = None,
) -> None:
    if not job.content or not str(job.content.get("uuid", "")).strip():
        raise ValueError("check_order 잡에 content.uuid 가 필요합니다.")
    order_uuid = str(job.content["uuid"]).strip()
    market = str(job.content.get("market", ""))
    side = str(job.content.get("side", ""))
    delay_sec = int(job.content.get("delay_sec", 30))
    retry_count = int(job.content.get("retry_count", 0))

    # 지연 대기 (첫 체크는 30초 후)
    if delay_sec > 0:
        await asyncio.sleep(delay_sec)

    if rate_limiter is not None:
        await rate_limiter.acquire()
    data = await asyncio.to_thread(_get_order_sync, settings, order_uuid)
    job.status = JobStatus.Complete.value
    job.response = {"order": data}

    state = str(data.get("state", ""))
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    side_ko = "매수" if side == "bid" else "매도"

    # 빗썸 주문 상태: wait(대기), watch(예약), done(완료), cancel(취소)
    if state == "done":
        # 체결 완료 → Trade 기록
        if session is not None:
            from datetime import UTC, datetime
            from decimal import Decimal

            from models.trade import Trade

            executed_vol = data.get("executed_volume", "0")
            trades_list = data.get("trades", [])
            avg_price = 0.0
            if trades_list:
                total_funds = sum(float(t.get("funds", 0)) for t in trades_list)
                total_vol = sum(float(t.get("volume", 0)) for t in trades_list)
                avg_price = total_funds / total_vol if total_vol > 0 else 0

            trade = Trade(
                market=market,
                side="buy" if side == "bid" else "sell",
                volume=Decimal(str(executed_vol)),
                price_krw=Decimal(str(round(avg_price, 4))) if avg_price else None,
                status="done",
                exchange_order_id=order_uuid,
                executed_at=datetime.now(UTC),
                payload=data,
            )
            session.add(trade)
            logger.info(
                "Trade 기록: %s %s vol=%s price=%s",
                market, side, executed_vol, avg_price,
            )

        if token and chat_id:
            executed_vol = data.get("executed_volume", "?")
            paid = data.get("paid_fee", "?")
            await publish_telegram(
                chat_id,
                f"✅ `{market}` {side_ko} 체결 완료\n"
                f"체결 수량: `{executed_vol}`\n"
                f"수수료: `{paid}`\n"
                f"주문 ID: `{order_uuid}`",
            )

    elif state in ("wait", "watch"):
        # 미체결 — 최대 2회 재확인 후 사용자에게 알림
        if retry_count < 2 and session is not None:
            # 30초 후 재확인
            recheck_job = Job(
                type="check_order",
                status=JobStatus.Ready.value,
                content={
                    "uuid": order_uuid,
                    "market": market,
                    "side": side,
                    "ord_type": job.content.get("ord_type", ""),
                    "delay_sec": 30,
                    "retry_count": retry_count + 1,
                },
                priority=2,
            )
            session.add(recheck_job)
            logger.info(
                "주문 미체결, 재확인 예약: %s %s (retry %d/2)",
                market, order_uuid[:8], retry_count + 1,
            )
        elif token and chat_id:
            # 재확인 2회 후에도 미체결 → 사용자 알림
            remaining_vol = data.get("remaining_volume", "?")
            executed_vol = data.get("executed_volume", "0")
            buttons = {
                "inline_keyboard": [
                    [
                        {
                            "text": "🔄 취소 후 시장가 재주문",
                            "callback_data": f"order_retry:{order_uuid}:{market}:{side}",
                        },
                        {
                            "text": "⏭️ 유지 (대기)",
                            "callback_data": f"order_keep:{order_uuid}",
                        },
                    ],
                    [
                        {
                            "text": "❌ 취소만",
                            "callback_data": f"order_cancel:{order_uuid}:{market}",
                        },
                    ],
                ],
            }
            await publish_telegram(
                chat_id,
                f"⚠️ `{market}` {side_ko} 주문 미체결\n"
                f"체결: `{executed_vol}` / 잔량: `{remaining_vol}`\n"
                f"주문 ID: `{order_uuid}`\n\n"
                f"1분 경과 후에도 체결되지 않았습니다.",
                reply_markup=buttons,
            )

    elif state == "cancel":
        if token and chat_id:
            await publish_telegram(
                chat_id,
                f"❌ `{market}` {side_ko} 주문 취소됨\n"
                f"주문 ID: `{order_uuid}`",
            )


async def handle_cancel_order(
    job: Job,
    settings: Settings,
    *,
    rate_limiter: AsyncSlidingWindowRateLimiter | None,
    session: AsyncSession | None = None,
) -> None:
    """주문 취소 + 선택적 시장가 재주문."""
    if not job.content or not str(job.content.get("uuid", "")).strip():
        raise ValueError("cancel_order 잡에 content.uuid 가 필요합니다.")

    order_uuid = str(job.content["uuid"]).strip()
    market = str(job.content.get("market", ""))
    side = str(job.content.get("side", ""))
    reorder = bool(job.content.get("reorder", False))
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    # 1) 주문 취소
    if rate_limiter is not None:
        await rate_limiter.acquire()
    try:
        cancel_data = await asyncio.to_thread(_cancel_order_sync, settings, order_uuid)
    except Exception:
        logger.warning("주문 취소 실패: %s", order_uuid, exc_info=True)
        if token and chat_id:
            await publish_telegram(
                chat_id,
                f"❌ 주문 취소 실패\n주문 ID: `{order_uuid}`\n이미 체결되었을 수 있습니다.",
            )
        job.status = JobStatus.Complete.value
        job.response = {"error": "cancel_failed"}
        return

    job.status = JobStatus.Complete.value
    job.response = {"cancel": cancel_data}

    side_ko = "매수" if side == "bid" else "매도"
    if token and chat_id:
        await publish_telegram(
            chat_id,
            f"✅ `{market}` {side_ko} 주문 취소 완료\n주문 ID: `{order_uuid}`",
        )

    # 2) 재주문 (시장가)
    if reorder and session is not None and market and side:
        # 잔고 기반으로 재주문 → check_balance 체이닝
        reorder_job = Job(
            type="check_balance",
            status=JobStatus.Ready.value,
            content={
                "trigger": "telegram",
                "market": market,
                "side": "BUY" if side == "bid" else "SELL",
                "price": 0,  # 시장가이므로 가격 무관
            },
            priority=1,
        )
        session.add(reorder_job)
        if token and chat_id:
            await publish_telegram(
                chat_id,
                f"🔄 `{market}` 시장가 재주문 진행 중...",
            )
