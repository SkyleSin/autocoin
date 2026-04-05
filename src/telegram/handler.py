"""텔레그램 명령어/콜백 핸들러."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.signal import Signal
from models.tracked_asset import TrackedAsset
from models.trade import Trade
from services.config_service import save_risk_setting
from telegram.bot import send_message
from utils.config import Settings

logger = logging.getLogger(__name__)


async def handle_update(
    update: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """텔레그램 업데이트 하나를 처리."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    # 콜백 쿼리 (인라인 버튼 클릭)
    if "callback_query" in update:
        await _handle_callback(update["callback_query"], session_factory, settings)
        return

    # 일반 메시지
    msg = update.get("message")
    if not msg or "text" not in msg:
        return

    text = msg["text"].strip()
    msg_chat_id = str(msg["chat"]["id"])

    # 권한 확인 (설정된 chat_id만 허용)
    if msg_chat_id != chat_id:
        logger.warning("telegram: 비인가 chat_id=%s 무시", msg_chat_id)
        return

    # 명령어 라우팅
    if text.startswith("/start") or text.startswith("/help"):
        await _cmd_help(token, chat_id)
    elif text.startswith("/balance"):
        await _cmd_balance(token, chat_id, settings)
    elif text.startswith("/assets"):
        await _cmd_assets(token, chat_id, session_factory)
    elif text.startswith("/add "):
        market = text[5:].strip().upper()
        await _cmd_add_asset(token, chat_id, session_factory, market)
    elif text.startswith("/remove "):
        market = text[8:].strip().upper()
        await _cmd_remove_asset(token, chat_id, session_factory, market)
    elif text.startswith("/risk"):
        await _cmd_risk(token, chat_id, settings, text, session_factory)
    elif text.startswith("/history"):
        await _cmd_history(token, chat_id, session_factory)
    elif text.startswith("/status"):
        await _cmd_status(token, chat_id, session_factory, settings)
    else:
        await send_message(token, chat_id, "알 수 없는 명령어입니다. /help 를 입력하세요.")


# ── 명령어 핸들러 ──


async def _cmd_help(token: str, chat_id: str) -> None:
    msg = """🤖 *autocoin 봇 명령어*

*자산 관리:*
/assets — 추적 중인 자산 목록
/add KRW-BTC — 추적 자산 추가
/remove KRW-BTC — 추적 자산 제거

*조회:*
/balance — 잔고 조회
/status — 시스템 상태
/history — 최근 매매 이력

*리스크 설정:*
/risk — 현재 리스크 설정 보기
/risk max\\_pct 10 — 1회 매매 상한 변경 (%)
/risk daily 20 — 일일 매매 횟수 변경
/risk reserve 50000 — 최소 유지 잔고 변경 (원)"""
    await send_message(token, chat_id, msg)


async def _cmd_balance(token: str, chat_id: str, settings: Settings) -> None:
    try:
        import asyncio

        from job_runner.workers import PrivateRequester

        def _fetch(s: Settings) -> list[dict]:
            with PrivateRequester(s) as prv:
                return prv.request("GET", "/v1/accounts")

        accounts = await asyncio.to_thread(_fetch, settings)
        lines = ["💰 *잔고 현황*\n"]
        for acc in accounts:
            currency = acc.get("currency", "?")
            balance = float(acc.get("balance", 0))
            locked = float(acc.get("locked", 0))
            if balance > 0 or locked > 0:
                lines.append(f"`{currency}`: {balance:,.4f} (묶임: {locked:,.4f})")
        await send_message(token, chat_id, "\n".join(lines))
    except Exception as e:
        await send_message(token, chat_id, f"❌ 잔고 조회 실패: {e}")


async def _cmd_assets(
    token: str, chat_id: str, sf: async_sessionmaker[AsyncSession],
) -> None:
    async with sf() as session:
        result = await session.execute(
            select(TrackedAsset).order_by(TrackedAsset.market)
        )
        assets = result.scalars().all()

    if not assets:
        await send_message(
            token, chat_id,
            "📋 추적 중인 자산이 없습니다.\n/add KRW-BTC 로 추가하세요.",
        )
        return

    lines = ["📋 *추적 자산 목록*\n"]
    for a in assets:
        status = "✅" if a.is_active else "⏸️"
        lines.append(f"{status} `{a.market}` ({a.intent})")
    await send_message(token, chat_id, "\n".join(lines))


async def _cmd_add_asset(
    token: str, chat_id: str, sf: async_sessionmaker[AsyncSession], market: str,
) -> None:
    if not market or "-" not in market:
        await send_message(token, chat_id, "❌ 형식: /add KRW-BTC")
        return

    async with sf() as session:
        existing = (await session.execute(
            select(TrackedAsset).where(
                TrackedAsset.market == market, TrackedAsset.is_active.is_(True),
            )
        )).scalar_one_or_none()

        if existing:
            await send_message(token, chat_id, f"⚠️ `{market}` 이미 추적 중입니다.")
            return

        asset = TrackedAsset(market=market, intent="watch", is_active=True)
        session.add(asset)
        await session.commit()

    await send_message(token, chat_id, f"✅ `{market}` 추적 자산에 추가되었습니다.")


async def _cmd_remove_asset(
    token: str, chat_id: str, sf: async_sessionmaker[AsyncSession], market: str,
) -> None:
    if not market:
        await send_message(token, chat_id, "❌ 형식: /remove KRW-BTC")
        return

    async with sf() as session:
        existing = (await session.execute(
            select(TrackedAsset).where(
                TrackedAsset.market == market, TrackedAsset.is_active.is_(True),
            )
        )).scalar_one_or_none()

        if not existing:
            await send_message(token, chat_id, f"⚠️ `{market}` 은 추적 목록에 없습니다.")
            return

        existing.is_active = False
        await session.commit()

    await send_message(token, chat_id, f"🗑️ `{market}` 추적을 중단했습니다.")


async def _cmd_risk(
    token: str,
    chat_id: str,
    settings: Settings,
    text: str,
    sf: async_sessionmaker[AsyncSession],
) -> None:
    parts = text.split()

    # /risk 만 입력 → 현재 설정 보기
    if len(parts) == 1:
        msg = f"""⚙️ *리스크 설정* (DB 저장)

• 1회 매매 상한: 잔고의 `{settings.risk_max_per_trade_pct}%`
• 일일 매매 횟수: `{settings.risk_max_daily_trades}회`
• 최소 유지 잔고: `{settings.risk_min_krw_reserve:,.0f}원`

변경: `/risk max_pct 10` `/risk daily 20` `/risk reserve 50000`"""
        await send_message(token, chat_id, msg)
        return

    if len(parts) < 3:
        await send_message(token, chat_id, "❌ 형식: /risk max\\_pct 10")
        return

    key_alias = parts[1]
    try:
        value = float(parts[2])
    except ValueError:
        await send_message(token, chat_id, "❌ 숫자를 입력하세요.")
        return

    # alias → 실제 DB 키 매핑
    alias_map = {
        "max_pct": "risk_max_per_trade_pct",
        "daily": "risk_max_daily_trades",
        "reserve": "risk_min_krw_reserve",
    }
    db_key = alias_map.get(key_alias)
    if not db_key:
        await send_message(
            token, chat_id, "❌ 키: `max_pct`, `daily`, `reserve`",
        )
        return

    # 범위 검증
    if key_alias == "max_pct" and not (0.1 <= value <= 100):
        await send_message(token, chat_id, "❌ 0.1 ~ 100 범위로 입력하세요.")
        return
    if key_alias == "daily" and value < 1:
        await send_message(token, chat_id, "❌ 1 이상 입력하세요.")
        return
    if key_alias == "reserve" and value < 0:
        await send_message(token, chat_id, "❌ 0 이상 입력하세요.")
        return

    # DB 저장 + Settings 반영
    async with sf() as session:
        await save_risk_setting(session, settings, db_key, value)

    label_map = {
        "max_pct": f"1회 매매 상한 → 잔고의 `{value}%`",
        "daily": f"일일 매매 횟수 → `{int(value)}회`",
        "reserve": f"최소 유지 잔고 → `{value:,.0f}원`",
    }
    await send_message(
        token, chat_id, f"✅ {label_map[key_alias]} (DB 저장됨)",
    )


async def _cmd_history(
    token: str, chat_id: str, sf: async_sessionmaker[AsyncSession],
) -> None:
    async with sf() as session:
        result = await session.execute(
            select(Trade).order_by(Trade.created_at.desc()).limit(10)
        )
        trades = result.scalars().all()

    if not trades:
        await send_message(token, chat_id, "📜 매매 이력이 없습니다.")
        return

    lines = ["📜 *최근 매매 이력*\n"]
    for t in trades:
        side_emoji = "🟢" if t.side == "buy" else "🔴"
        lines.append(
            f"{side_emoji} `{t.market}` {t.side} "
            f"v={t.volume} p={t.price_krw} "
            f"[{t.status}] {t.created_at:%m/%d %H:%M}"
        )
    await send_message(token, chat_id, "\n".join(lines))


async def _cmd_status(
    token: str,
    chat_id: str,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async with sf() as session:
        # 추적 자산 수
        asset_count = (await session.execute(
            select(TrackedAsset).where(TrackedAsset.is_active.is_(True))
        )).scalars().all()

        # 최근 시그널
        last_signal = (await session.execute(
            select(Signal).order_by(Signal.created_at.desc()).limit(1)
        )).scalar_one_or_none()

    lines = ["📊 *시스템 상태*\n"]
    lines.append(f"• 추적 자산: {len(asset_count)}개")
    lines.append(f"• 캔들 수집 주기: {settings.candle_interval_sec:.0f}초")
    lines.append(f"• LLM 모델: `{settings.ollama_model}`")

    if last_signal:
        lines.append(
            f"\n*최근 시그널:*\n"
            f"  {last_signal.market} → {last_signal.signal_type} "
            f"(conf={last_signal.confidence}, source={last_signal.source})\n"
            f"  {last_signal.created_at:%Y-%m-%d %H:%M:%S}"
        )
    else:
        lines.append("\n아직 시그널이 없습니다.")

    await send_message(token, chat_id, "\n".join(lines))


# ── 콜백 핸들러 (인라인 버튼) ──


async def _handle_callback(
    callback: dict[str, Any],
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """인라인 버튼 콜백 처리."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    callback_id = callback.get("id", "")
    data = callback.get("data", "")

    # 콜백 응답 (버튼 로딩 해제)
    await _answer_callback(token, callback_id)

    if data.startswith("trade_execute:"):
        # trade_execute:{market}:{side}:{price}
        parts = data.split(":")
        if len(parts) >= 4:
            market = parts[1]
            side = parts[2]
            price = float(parts[3])
            await send_message(
                token, chat_id,
                f"⏳ `{market}` {side} 주문을 준비합니다...\n"
                f"현재가: `{price:,.0f} KRW`\n\n"
                f"잔고 확인 후 리스크 검증을 진행합니다.",
            )
            # 실제 주문은 place_order Job으로 처리
            async with sf() as session:
                from models.job import Job
                from models.job_status import JobStatus
                job = Job(
                    type="check_balance",
                    status=JobStatus.Ready.value,
                    content={
                        "trigger": "telegram",
                        "market": market,
                        "side": side,
                        "price": price,
                    },
                    priority=5,
                )
                session.add(job)
                await session.commit()
    elif data == "trade_pass":
        await send_message(token, chat_id, "⏭️ 시그널을 패스했습니다.")
    else:
        logger.warning("telegram: 알 수 없는 콜백 data=%s", data)


async def _answer_callback(token: str, callback_id: str) -> None:
    """텔레그램 콜백 쿼리 응답 (버튼 로딩 해제)."""
    import httpx
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"callback_query_id": callback_id})
    except Exception:
        logger.warning("콜백 응답 실패", exc_info=True)
