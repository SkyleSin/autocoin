"""긴급 매도 안전장치 — 지지선 이탈 감시 (1분 주기)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.strategy.llm.emergency_prompt import build_emergency_prompt
from infra.redis_client import get_redis
from infra.redis_pubsub import publish_telegram
from infra.redis_queue import push_trade_job
from models.signal import Signal
from utils.config import Settings

logger = logging.getLogger(__name__)


def _get_balances_sync(settings: Settings) -> list[dict[str, Any]]:
    from bithumb.account import BithumbAccount
    client = BithumbAccount(settings)
    return client.get_balances()


def _get_ticker_sync(settings: Settings, market: str) -> float:
    from bithumb.ticker import BithumbTicker
    client = BithumbTicker(settings)
    data = client.get(market)
    if data and isinstance(data, list):
        return float(data[0].get("trade_price", 0))
    return 0.0


async def _get_nearest_support(
    session: AsyncSession, market: str,
) -> tuple[float | None, dict[str, Any] | None]:
    """최근 시그널에서 지지선과 지표 정보 추출."""
    last_signal = (await session.execute(
        select(Signal)
        .where(Signal.market == market)
        .order_by(Signal.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    if not last_signal or not last_signal.indicators:
        return None, None

    sr = last_signal.indicators.get("support_resistance")
    if not sr or not isinstance(sr, dict):
        return None, last_signal.indicators

    support = sr.get("nearest_support")
    if support is None:
        return None, last_signal.indicators

    return float(support), last_signal.indicators


async def _call_emergency_llm(
    settings: Settings, prompt: str,
) -> Any:
    """DeepSeek 우선 → Ollama 폴백."""
    result = None

    if settings.deepseek_api_key:
        try:
            from core.strategy.llm.deepseek_llm import query_deepseek
            result = await query_deepseek(
                prompt,
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                timeout=settings.deepseek_timeout,
            )
        except Exception:
            logger.warning("긴급판단: DeepSeek 실패, Ollama 폴백", exc_info=True)

    if result is None:
        from core.strategy.llm.local_llm import query_ollama
        result = await query_ollama(
            prompt,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
            num_gpu=settings.ollama_num_gpu,
        )

    return result


async def _execute_emergency_sell(
    session: AsyncSession,
    settings: Settings,
    market: str,
    balance: float,
) -> None:
    """긴급 시장가 매도 — Redis 매매 큐로 전송."""
    await push_trade_job({
        "type": "execute_trade",
        "user_id": "master",
        "content": {
            "market": market,
            "side": "ask",
            "volume": str(balance),
            "ord_type": "market",
        },
    })
    logger.info("긴급 매도 Redis push: %s vol=%s", market, balance)


async def _check_single_asset(
    session: AsyncSession,
    settings: Settings,
    currency: str,
    balance: float,
) -> None:
    """단일 보유 자산의 지지선 이탈 확인."""
    market = f"KRW-{currency}"
    chat_id = settings.telegram_chat_id

    # 현재가 조회
    try:
        price = await asyncio.to_thread(_get_ticker_sync, settings, market)
    except Exception:
        logger.debug("safety: %s 현재가 조회 실패", market)
        return

    if price <= 0:
        return

    # 지지선 조회
    support, indicators = await _get_nearest_support(session, market)
    if support is None or support <= 0:
        return

    # 이탈 폭 계산
    breach_pct = (support - price) / support * 100
    r = get_redis()

    # 지지선 위 → 카운터 리셋
    if breach_pct < settings.safety_breach_consecutive_pct:
        prev = await r.get(f"breach:{market}")
        if prev and int(prev) > 0:
            logger.debug("safety: %s 지지선 복귀 (%.1f%%)", market, breach_pct)
        await r.delete(f"breach:{market}")
        return

    # 쿨다운 확인 (TTL 기반)
    if await r.exists(f"safety_triggered:{market}"):
        return

    # 즉시 트리거 (3%+ 이탈)
    consecutive = int(await r.get(f"breach:{market}") or 0)

    if breach_pct >= settings.safety_breach_immediate_pct:
        logger.warning(
            "safety: %s 즉시 트리거 — 지지선 %.0f 대비 %.1f%% 이탈 (현재 %.0f)",
            market, support, breach_pct, price,
        )
    else:
        # 1~3% 연속 이탈
        consecutive = await r.incr(f"breach:{market}")
        if consecutive >= settings.safety_breach_consecutive_count:
            logger.warning(
                "safety: %s 연속 트리거 — %d분 연속 지지선 이탈 (%.1f%%)",
                market, consecutive, breach_pct,
            )
        else:
            logger.info(
                "safety: %s 지지선 이탈 감시 중 (%d/%d, %.1f%%)",
                market, consecutive, settings.safety_breach_consecutive_count, breach_pct,
            )
            return

    # LLM 긴급 판단
    await r.set(
        f"safety_triggered:{market}", 1, ex=settings.safety_cooldown_sec,
    )
    await r.delete(f"breach:{market}")

    prompt = build_emergency_prompt(
        market=market,
        current_price=price,
        support_level=support,
        breach_pct=breach_pct,
        consecutive_minutes=consecutive,
        indicators=indicators,
    )

    try:
        llm_result = await _call_emergency_llm(settings, prompt)
    except Exception:
        logger.exception("safety: %s LLM 호출 실패 — 매도 보류", market)
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚠️ `{market}` 지지선 이탈 감지 ({breach_pct:.1f}%)\n"
                f"LLM 호출 실패 — 수동 확인 필요",
            )
        return

    if llm_result.decision == "SELL":
        # 자동 매도 실행
        await _execute_emergency_sell(session, settings, market, balance)
        if chat_id:
            await publish_telegram(
                chat_id,
                f"🔴 *긴급 매도 실행*\n\n"
                f"마켓: `{market}`\n"
                f"현재가: `{price:,.0f}`\n"
                f"지지선: `{support:,.0f}` (이탈 {breach_pct:.1f}%)\n"
                f"매도 수량: `{balance}`\n\n"
                f"*LLM 판단:* {llm_result.reason}\n"
                f"확신도: {llm_result.confidence:.0%}",
            )
    else:
        # HOLD — 경고만
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚠️ *지지선 이탈 감지*\n\n"
                f"마켓: `{market}`\n"
                f"현재가: `{price:,.0f}`\n"
                f"지지선: `{support:,.0f}` (이탈 {breach_pct:.1f}%)\n\n"
                f"*LLM 판단: 유지*\n{llm_result.reason}\n"
                f"확신도: {llm_result.confidence:.0%}",
            )


async def safety_monitor(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """보유 코인 지지선 이탈 감시 루프 (1분 주기)."""
    interval = settings.safety_monitor_interval_sec
    logger.info("safety_monitor: started (interval=%.0fs)", interval)

    try:
        while True:
            try:
                # 보유 자산 조회
                accounts = await asyncio.to_thread(_get_balances_sync, settings)
                held = [
                    (acc["currency"], float(acc["balance"]))
                    for acc in accounts
                    if acc.get("currency") != "KRW" and float(acc.get("balance", 0)) > 0
                ]

                if held:
                    async with session_factory() as session:
                        for currency, balance in held:
                            await _check_single_asset(
                                session, settings, currency, balance,
                            )
                        await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("safety_monitor: error in cycle")

            await asyncio.sleep(interval)
    finally:
        logger.info("safety_monitor stopped")
