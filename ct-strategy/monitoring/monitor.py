"""긴급 매도 안전장치 — 지지선 이탈 감시 (1분 주기).

판단만 수행하고, 실행(매도/알림)은 advisor_request로 위임한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from infra.api.portfolio import get_user_portfolio
from infra.api.signal import get_latest_signal
from infra.api.tracked_asset import get_active_markets, update_market_prices
from infra.api.user import get_active_users
from infra.redis_client import get_redis
from infra.redis_pubsub import publish_defense_request
from utils.config import Settings

from monitoring.llm.emergency_prompt import build_emergency_prompt

logger = logging.getLogger(__name__)


def _get_ticker_sync(settings: Settings, market: str) -> float:
    import httpx

    url = f"{settings.bithumb_base_url}/v1/ticker"
    params = {"markets": market}
    resp = httpx.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data and isinstance(data, list):
        return float(data[0].get("trade_price", 0))
    return 0.0


def _get_tickers_sync(settings: Settings, markets: list[str]) -> dict[str, float]:
    """여러 마켓 현재가 일괄 조회."""
    import httpx

    if not markets:
        return {}
    url = f"{settings.bithumb_base_url}/v1/ticker"
    params = {"markets": ",".join(markets)}
    resp = httpx.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {
        item["market"]: float(item.get("trade_price", 0))
        for item in data
        if isinstance(item, dict)
    }


async def _get_nearest_support(
    market: str,
) -> tuple[float | None, dict[str, Any] | None]:
    """최근 시그널에서 지지선과 지표 정보 추출."""
    last_signal = await get_latest_signal(market)

    if not last_signal or not last_signal.get("indicators"):
        return None, None

    indicators = last_signal["indicators"]
    sr = indicators.get("support_resistance")
    if not sr or not isinstance(sr, dict):
        return None, indicators

    support = sr.get("nearest_support")
    if support is None:
        return None, indicators

    return float(support), indicators


async def _call_emergency_llm(
    settings: Settings, prompt: str,
) -> Any:
    """DeepSeek 우선 → Ollama 폴백."""
    result = None

    if settings.deepseek_api_key:
        try:
            from monitoring.llm.deepseek_llm import query_deepseek
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
        from monitoring.llm.local_llm import query_ollama
        result = await query_ollama(
            prompt,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
            num_gpu=settings.ollama_num_gpu,
        )

    return result


async def _check_single_asset(
    settings: Settings,
    user_id: int,
    chat_id: str,
    currency: str,
    balance: float,
) -> None:
    """단일 보유 자산의 지지선 이탈 확인 → 판단 결과를 advisor_request로 위임."""
    market = f"KRW-{currency}"

    # 현재가 조회
    try:
        price = await asyncio.to_thread(_get_ticker_sync, settings, market)
    except Exception:
        logger.debug("safety: %s 현재가 조회 실패", market)
        return

    if price <= 0:
        return

    # 지지선 조회
    support, indicators = await _get_nearest_support(market)
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
        await publish_defense_request({
            "type": "safety_llm_fail",
            "user_id": user_id,
            "chat_id": chat_id,
            "market": market,
            "breach_pct": breach_pct,
        })
        return

    # 판단 결과를 advisor_request로 위임
    await publish_defense_request({
        "type": "safety",
        "user_id": user_id,
        "chat_id": chat_id,
        "market": market,
        "currency": currency,
        "balance": balance,
        "current_price": price,
        "support_level": support,
        "breach_pct": breach_pct,
        "llm_decision": llm_result.decision,
        "llm_reason": llm_result.reason,
        "llm_confidence": llm_result.confidence,
    })


async def _update_tracked_prices(settings: Settings) -> None:
    """활성 추적 종목의 현재가를 빗썸에서 조회하여 DB 갱신."""
    try:
        assets = await get_active_markets()
        markets = [a["market"] for a in assets if a.get("market")]
        if not markets:
            return
        prices = await asyncio.to_thread(_get_tickers_sync, settings, markets)
        items = [
            {"market": m, "price": p} for m, p in prices.items() if p > 0
        ]
        if items:
            await update_market_prices(items)
            logger.debug("현재가 갱신: %d종목", len(items))
    except Exception:
        logger.warning("현재가 갱신 실패", exc_info=True)


async def safety_monitor(settings: Settings) -> None:
    """보유 코인 지지선 이탈 감시 루프 (1분 주기)."""
    interval = settings.safety_monitor_interval_sec
    logger.info("safety_monitor: started (interval=%.0fs)", interval)

    try:
        while True:
            try:
                await _update_tracked_prices(settings)

                users = await get_active_users()
                for user in users:
                    user_id = user.get("id")
                    chat_id = str(user.get("telegram_id", ""))
                    if not user_id:
                        continue

                    portfolio = await get_user_portfolio(user_id)
                    held = [
                        (item["currency"], float(item["balance"]))
                        for item in portfolio
                        if item.get("currency") != "KRW"
                        and float(item.get("balance", 0)) > 0
                    ]

                    for currency, balance in held:
                        await _check_single_asset(
                            settings, user_id, chat_id, currency, balance,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("safety_monitor: error in cycle")

            await asyncio.sleep(interval)
    finally:
        logger.info("safety_monitor stopped")
