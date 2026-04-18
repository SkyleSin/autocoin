"""뉴스 충격 감지 모니터 — 1분 주기 거래량/변동성/갭 감시."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from safety.defense_mode import (
    ShockLevel,
    calculate_dynamic_stop_pct,
    calculate_stop_loss,
    check_recovery_conditions,
    check_stop_loss_hit,
    enter_defense,
    exit_defense,
    get_defense_state,
    is_defense_active,
    update_trailing,
)
from safety.defense_mode import (
    _save_state as _save_defense_state,
)
from safety.shock_detector import (
    classify_shock,
    detect_price_gap,
    detect_volatility_surge,
    detect_volume_surge,
)
from safety.llm.shock_prompt import build_shock_prompt
from infra.api.candle import get_candles
from infra.api.indicator_snapshot import get_latest_snapshot
from infra.api.portfolio import get_user_portfolio
from infra.api.signal import get_latest_signal
from infra.api.user import get_active_users
from infra.redis_client import get_redis
from infra.redis_pubsub import publish_telegram
from infra.redis_queue import push_trade_job
from utils.config import Settings

logger = logging.getLogger(__name__)


# ── 데이터 조회 헬퍼 ──


def _get_candles_1m_sync(settings: Settings, market: str, count: int = 30) -> list:
    """빗썸 1분봉 최근 N개 조회 (동기)."""
    import httpx

    url = f"{settings.bithumb_base_url}/v1/candles/minutes/1"
    params = {"market": market, "count": count}
    resp = httpx.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return list(reversed(data))  # 시간순 정렬
    return []


async def _get_indicator_snapshot(
    market: str, tf: str,
) -> dict[str, Any] | None:
    """indicator_snapshots API에서 최근 지표 조회."""
    return await get_latest_snapshot(market, tf)


async def _get_nearest_support(market: str) -> float | None:
    """최근 시그널에서 지지선 추출."""
    last_signal = await get_latest_signal(market)
    if not last_signal or not last_signal.get("indicators"):
        return None

    sr = last_signal["indicators"].get("support_resistance")
    if not sr or not isinstance(sr, dict):
        return None
    support = sr.get("nearest_support")
    return float(support) if support else None


async def _get_daily_candles(market: str, days: int = 10) -> list[dict]:
    """최근 N일 일봉 조회 (ct-api)."""
    candles = await get_candles(market, "1d", limit=days, order="desc")
    return list(reversed(candles)) if candles else []


async def _get_pre_shock_bb_width(market: str) -> float:
    """충격 전 3시간 평균 BB 폭 (1H 기준). 없으면 0."""
    snap = await _get_indicator_snapshot(market, "1h")
    if not snap:
        return 0.0
    bb_u = snap.get("bb_upper")
    bb_l = snap.get("bb_lower")
    bb_m = snap.get("bb_middle")
    if bb_u and bb_l and bb_m and float(bb_m) > 0:
        return (float(bb_u) - float(bb_l)) / float(bb_m)
    return 0.0


async def _get_pre_shock_volume_avg(market: str) -> float:
    """충격 전 거래량 비율 평균. volume_ratio에서 추출."""
    snap = await _get_indicator_snapshot(market, "1h")
    if not snap:
        return 0.0
    return float(snap.get("volume_ratio", 1.0))


# ── LLM 호출 ──


async def _call_shock_llm(settings: Settings, prompt: str) -> Any:
    """DeepSeek 우선 → Ollama 폴백."""
    result = None

    if settings.deepseek_api_key:
        try:
            from safety.llm.deepseek_llm import query_deepseek
            result = await query_deepseek(
                prompt,
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                timeout=settings.deepseek_timeout,
            )
        except Exception:
            logger.warning("shock_llm: DeepSeek 실패, Ollama 폴백", exc_info=True)

    if result is None:
        from safety.llm.local_llm import query_ollama
        result = await query_ollama(
            prompt,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
            num_gpu=settings.ollama_num_gpu,
        )

    return result


# ── 충격 감지 + 대응 ──


async def _detect_and_handle_shock(
    settings: Settings,
    user_id: int,
    chat_id: str,
    market: str,
    currency: str,
    balance: float,
) -> None:
    """단일 마켓 충격 감지 + 방어 모드 진입."""
    r = get_redis()
    if await r.exists(f"shock_cooldown:{market}"):
        return

    # 1분봉 조회
    try:
        candles_1m = await asyncio.to_thread(
            _get_candles_1m_sync, settings, market, 30,
        )
    except Exception:
        logger.debug("shock: %s 1분봉 조회 실패", market)
        return

    if len(candles_1m) < 5:
        return

    # 3가지 신호 감지
    vol_ok, vol_ratio = detect_volume_surge(
        candles_1m, settings.shock_volume_threshold,
    )
    volat_ok, volat_ratio = detect_volatility_surge(
        candles_1m, settings.shock_volatility_threshold,
    )
    gap_ok, gap_pct = detect_price_gap(
        candles_1m, settings.shock_gap_threshold_pct,
    )

    shock = classify_shock(
        vol_ok, vol_ratio,
        volat_ok, volat_ratio,
        gap_ok, gap_pct,
        settings.shock_panic_gap_pct,
    )

    if shock.level == ShockLevel.NONE:
        return

    await r.set(
        f"shock_cooldown:{market}", 1, ex=settings.shock_cooldown_sec,
    )
    current_price = float(candles_1m[-1].get("trade_price", 0))

    logger.warning(
        "shock detected: %s level=%s signals=%s price=%.0f",
        market, shock.level.value, shock.triggered_signals, current_price,
    )

    # 지표 조회 (LLM 프롬프트용)
    snap_4h = await _get_indicator_snapshot(market, "4h")
    snap_1h = await _get_indicator_snapshot(market, "1h")
    snap_15m = await _get_indicator_snapshot(market, "15m")

    position_info = f"{currency} {balance}개 보유"

    prompt = build_shock_prompt(
        market=market,
        current_price=current_price,
        shock_level=shock.level.value,
        shock_signals=shock.triggered_signals,
        volume_ratio=vol_ratio,
        volatility_ratio=volat_ratio,
        gap_pct=gap_pct,
        indicators_4h=snap_4h,
        indicators_1h=snap_1h,
        indicators_15m=snap_15m,
        position_info=position_info,
    )

    try:
        llm_result = await _call_shock_llm(settings, prompt)
    except Exception:
        logger.exception("shock: %s LLM 호출 실패", market)
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚡ `{market}` 충격 감지 ({shock.level.value})\n"
                f"신호: {', '.join(shock.triggered_signals)}\n"
                f"LLM 호출 실패 — 수동 확인 필요",
            )
        return

    if llm_result.decision != "SELL":
        if chat_id:
            await publish_telegram(
                chat_id,
                f"⚡ *충격 감지 — HOLD 판단*\n\n"
                f"마켓: `{market}` ({shock.level.value})\n"
                f"신호: {', '.join(shock.triggered_signals)}\n"
                f"현재가: `{current_price:,.0f}`\n\n"
                f"*LLM:* {llm_result.reason}\n"
                f"확신도: {llm_result.confidence:.0%}",
            )
        return

    # ── SELL 판단 → 방어 모드 진입 ──

    sr_support = await _get_nearest_support(market)
    daily_candles = await _get_daily_candles(market, 10)
    dynamic_pct = calculate_dynamic_stop_pct(daily_candles)

    pre_shock_atr = None
    if snap_15m:
        bb_u = snap_15m.get("bb_upper")
        bb_l = snap_15m.get("bb_lower")
        if bb_u and bb_l:
            pre_shock_atr = (float(bb_u) - float(bb_l)) / 4

    stop_loss = calculate_stop_loss(
        current_price, pre_shock_atr, sr_support, dynamic_pct, shock.level,
    )

    pre_bb = await _get_pre_shock_bb_width(market)
    pre_vol = await _get_pre_shock_volume_avg(market)

    state = await enter_defense(
        market, shock.level, shock,
        current_price, stop_loss, pre_bb, pre_vol,
    )

    # 강도별 즉시 대응
    if shock.level == ShockLevel.PANIC:
        await _execute_sell(user_id, market, balance, "전액 (패닉)")
        state.partial_sold_pct = 100.0
        await _save_defense_state(state)
    elif shock.level == ShockLevel.DANGER:
        sell_vol = balance * 0.5
        await _execute_sell(user_id, market, sell_vol, "50% (위험)")
        state.partial_sold_pct = 50.0
        await _save_defense_state(state)

    if chat_id:
        action = {
            ShockLevel.WARNING: "손절선 적용",
            ShockLevel.DANGER: "50% 청산 + 손절선",
            ShockLevel.PANIC: "전액 청산 권고",
        }.get(shock.level, "")

        await publish_telegram(
            chat_id,
            f"🛡️ *방어 모드 진입*\n\n"
            f"마켓: `{market}` ({shock.level.value})\n"
            f"신호: {', '.join(shock.triggered_signals)}\n"
            f"현재가: `{current_price:,.0f}`\n"
            f"손절선: `{stop_loss:,.0f}`\n"
            f"동적기준: {dynamic_pct:.1f}%\n"
            f"대응: {action}\n\n"
            f"*LLM:* {llm_result.reason}\n"
            f"확신도: {llm_result.confidence:.0%}",
        )


async def _execute_sell(
    user_id: int,
    market: str,
    volume: float,
    reason: str,
) -> None:
    """긴급 매도 — Redis 매매 큐로 전송."""
    await push_trade_job({
        "type": "place_order",
        "user_id": str(user_id),
        "payload": {
            "market": market,
            "side": "ask",
            "volume": str(volume),
            "ord_type": "market",
        },
    })
    logger.info("shock_sell Redis push: user=%d %s vol=%s (%s)", user_id, market, volume, reason)


# ── 방어 모드 관리 ──


async def _manage_defense(
    settings: Settings,
    user_id: int,
    chat_id: str,
    market: str,
    currency: str,
    balance: float,
) -> None:
    """방어 모드 활성 마켓 관리: 트레일링 + 손절 체크 + 복귀 조건."""
    state = await get_defense_state(market)
    if state is None:
        return

    # 현재가 조회 (1분봉에서 최신가)
    try:
        candles_1m = await asyncio.to_thread(
            _get_candles_1m_sync, settings, market, 1,
        )
        price = float(candles_1m[-1].get("trade_price", 0)) if candles_1m else 0.0
    except Exception:
        return

    if price <= 0:
        return

    # ATR 갱신 (15m BB 기반)
    snap_15m = await _get_indicator_snapshot(market, "15m")
    current_atr = None
    if snap_15m:
        bb_u = snap_15m.get("bb_upper")
        bb_l = snap_15m.get("bb_lower")
        if bb_u and bb_l:
            current_atr = (float(bb_u) - float(bb_l)) / 4

    await update_trailing(market, price, current_atr)

    if await check_stop_loss_hit(market, price):
        logger.warning(
            "shock: %s 손절선 이탈 — price=%.0f stop=%.0f",
            market, price, state.stop_loss_price,
        )
        remaining_pct = 100.0 - state.partial_sold_pct
        if remaining_pct > 0 and balance > 0:
            await _execute_sell(user_id, market, balance, f"손절 ({remaining_pct:.0f}%)")
            state.partial_sold_pct = 100.0
            await _save_defense_state(state)

            if chat_id:
                await publish_telegram(
                    chat_id,
                    f"🔴 *손절 매도 실행*\n\n"
                    f"마켓: `{market}`\n"
                    f"현재가: `{price:,.0f}`\n"
                    f"손절선: `{state.stop_loss_price:,.0f}`\n"
                    f"매도량: `{balance}`",
                )

        await exit_defense(market)
        return

    # 복귀 조건 확인
    snap_1h = await _get_indicator_snapshot(market, "1h")
    current_bb_width = None
    current_vol_avg = None

    if snap_1h:
        bb_u = snap_1h.get("bb_upper")
        bb_l = snap_1h.get("bb_lower")
        bb_m = snap_1h.get("bb_middle")
        if bb_u and bb_l and bb_m and float(bb_m) > 0:
            current_bb_width = (float(bb_u) - float(bb_l)) / float(bb_m)
        current_vol_avg = float(snap_1h.get("volume_ratio", 0))

    from safety.defense_mode import _RECOVERY_MIN_HOURS
    elapsed_h = (datetime.now(UTC) - state.entered_at).total_seconds() / 3600
    min_h = _RECOVERY_MIN_HOURS.get(state.level, 4)

    llm_ok = False
    if elapsed_h >= min_h and not state.recovery_llm_checked:
        try:
            prompt = build_shock_prompt(
                market=market,
                current_price=price,
                shock_level=f"{state.level.value} (복귀 검토)",
                shock_signals=state.shock_signal.triggered_signals,
                volume_ratio=state.shock_signal.volume_ratio,
                volatility_ratio=state.shock_signal.volatility_ratio,
                gap_pct=state.shock_signal.gap_pct,
                indicators_4h=await _get_indicator_snapshot(market, "4h"),
                indicators_1h=snap_1h,
                indicators_15m=snap_15m,
                position_info=f"{currency} {balance}개 보유",
            )
            result = await _call_shock_llm(settings, prompt)
            llm_ok = result.decision in ("HOLD", "BUY")
            state.recovery_llm_checked = True
            await _save_defense_state(state)
        except Exception:
            logger.exception("shock: %s 복귀 LLM 실패", market)

    if await check_recovery_conditions(
        market, current_bb_width, current_vol_avg, llm_ok,
    ):
        await exit_defense(market)
        if chat_id:
            await publish_telegram(
                chat_id,
                f"✅ *방어 모드 해제*\n\n"
                f"마켓: `{market}`\n"
                f"현재가: `{price:,.0f}`\n"
                f"정상 복귀 조건 충족",
            )


# ── 메인 루프 ──


async def shock_monitor(settings: Settings) -> None:
    """뉴스 충격 감지 모니터 루프 (1분 주기).

    유저별 포트폴리오를 DB에서 조회하여 감시.
    """
    interval = settings.shock_monitor_interval_sec
    logger.info("shock_monitor: started (interval=%.0fs)", interval)

    try:
        while True:
            try:
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
                        market = f"KRW-{currency}"
                        if await is_defense_active(market):
                            await _manage_defense(
                                settings, user_id, chat_id,
                                market, currency, balance,
                            )
                        else:
                            await _detect_and_handle_shock(
                                settings, user_id, chat_id,
                                market, currency, balance,
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("shock_monitor: error in cycle")

            await asyncio.sleep(interval)
    finally:
        logger.info("shock_monitor stopped")
