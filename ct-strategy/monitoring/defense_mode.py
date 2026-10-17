"""뉴스 충격 방어 모드 — Redis 상태 관리 + 손절선 계산."""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from infra.redis_client import get_redis

from .shock_detector import ShockLevel, ShockSignal

logger = logging.getLogger(__name__)

# ── 강도별 파라미터 ──
_STOP_LOSS_MULTIPLIER = {
    ShockLevel.WARNING: 1.0,
    ShockLevel.DANGER: 1.5,
    ShockLevel.PANIC: 2.0,
}

_TRAILING_DELAY = {
    ShockLevel.WARNING: timedelta(hours=1),
    ShockLevel.DANGER: timedelta(hours=2),
    ShockLevel.PANIC: timedelta(hours=8),
}

_RECOVERY_MIN_HOURS = {
    ShockLevel.WARNING: 2,
    ShockLevel.DANGER: 4,
    ShockLevel.PANIC: 8,
}

# 동적 기준% 클램핑
_MIN_STOP_PCT = 4.0
_MAX_STOP_PCT = 10.0
_DAILY_ATR_MULTIPLIER = 1.5


@dataclass
class DefenseState:
    """마켓별 방어 모드 상태."""

    market: str
    level: ShockLevel
    entered_at: datetime
    shock_signal: ShockSignal
    stop_loss_price: float
    highest_since_entry: float     # 트레일링용 최고가 추적
    pre_shock_bb_width_avg: float  # 충격 전 3h BB 폭 평균
    pre_shock_volume_avg: float    # 충격 전 6h 거래량 평균
    partial_sold_pct: float = 0.0  # 부분 청산 비율
    trailing_active: bool = False
    recovery_llm_checked: bool = False
    details: list[str] = field(default_factory=list)


# ── Redis 직렬화 헬퍼 ──


def _serialize_state(state: DefenseState) -> dict[str, str]:
    """DefenseState → Redis Hash 매핑."""
    return {
        "market": state.market,
        "level": state.level.value,
        "entered_at": state.entered_at.isoformat(),
        "shock_signal": json.dumps({
            "level": state.shock_signal.level.value,
            "triggered_signals": state.shock_signal.triggered_signals,
            "volume_ratio": state.shock_signal.volume_ratio,
            "volatility_ratio": state.shock_signal.volatility_ratio,
            "gap_pct": state.shock_signal.gap_pct,
        }),
        "stop_loss_price": str(state.stop_loss_price),
        "highest_since_entry": str(state.highest_since_entry),
        "pre_shock_bb_width_avg": str(state.pre_shock_bb_width_avg),
        "pre_shock_volume_avg": str(state.pre_shock_volume_avg),
        "partial_sold_pct": str(state.partial_sold_pct),
        "trailing_active": str(state.trailing_active),
        "recovery_llm_checked": str(state.recovery_llm_checked),
        "details": json.dumps(state.details),
    }


def _deserialize_state(data: dict[str, str]) -> DefenseState:
    """Redis Hash → DefenseState."""
    ss = json.loads(data["shock_signal"])
    return DefenseState(
        market=data["market"],
        level=ShockLevel(data["level"]),
        entered_at=datetime.fromisoformat(data["entered_at"]),
        shock_signal=ShockSignal(
            level=ShockLevel(ss["level"]),
            triggered_signals=ss["triggered_signals"],
            volume_ratio=ss["volume_ratio"],
            volatility_ratio=ss["volatility_ratio"],
            gap_pct=ss["gap_pct"],
        ),
        stop_loss_price=float(data["stop_loss_price"]),
        highest_since_entry=float(data["highest_since_entry"]),
        pre_shock_bb_width_avg=float(data["pre_shock_bb_width_avg"]),
        pre_shock_volume_avg=float(data["pre_shock_volume_avg"]),
        partial_sold_pct=float(data["partial_sold_pct"]),
        trailing_active=data["trailing_active"] == "True",
        recovery_llm_checked=data["recovery_llm_checked"] == "True",
        details=json.loads(data["details"]),
    )


# ── 상태 조회/관리 ──


async def is_defense_active(market: str) -> bool:
    r = get_redis()
    return bool(await r.exists(f"defense:{market}"))


async def is_any_defense_active() -> bool:
    r = get_redis()
    return (await r.scard("defense:active_set")) > 0


async def get_defense_state(market: str) -> DefenseState | None:
    r = get_redis()
    data = await r.hgetall(f"defense:{market}")
    if not data:
        return None
    return _deserialize_state(data)


async def get_all_defense_states() -> dict[str, DefenseState]:
    r = get_redis()
    markets = await r.smembers("defense:active_set")
    result: dict[str, DefenseState] = {}
    for m in markets:
        data = await r.hgetall(f"defense:{m}")
        if data:
            result[m] = _deserialize_state(data)
    return result


def calculate_dynamic_stop_pct(daily_candles: list[dict[str, Any]]) -> float:
    """최근 7일 일봉 ATR → 동적 손절 기준%.

    ATR / 현재가 × 100 × 1.5, 클램핑 4%~10%.
    """
    from analysis.atr import atr_from_candles

    if not daily_candles:
        return _MAX_STOP_PCT

    atr_val = atr_from_candles(daily_candles, period=min(7, len(daily_candles) - 1))
    if atr_val is None or atr_val <= 0:
        return _MAX_STOP_PCT

    current_price = float(daily_candles[-1]["close"])
    if current_price <= 0:
        return _MAX_STOP_PCT

    pct = atr_val / current_price * 100 * _DAILY_ATR_MULTIPLIER
    return max(_MIN_STOP_PCT, min(_MAX_STOP_PCT, round(pct, 1)))


def calculate_stop_loss(
    current_price: float,
    pre_shock_atr: float | None,
    sr_support: float | None,
    dynamic_stop_pct: float,
    level: ShockLevel,
) -> float:
    """3단 방어선의 median 계산.

    A = 현재가 - ATR_사전 × 2
    B = S/R 지지선 × 0.985
    C = 현재가 × (1 - 동적기준%)

    손절선 = median(후보들) × 강도별 multiplier 적용
    """
    multiplier = _STOP_LOSS_MULTIPLIER.get(level, 1.0)
    # multiplier가 클수록 손절 폭이 넓어짐 (더 아래)

    candidates = []

    # A: ATR 기반
    if pre_shock_atr and pre_shock_atr > 0:
        candidates.append(current_price - pre_shock_atr * 2)

    # B: S/R 기반
    if sr_support and sr_support > 0:
        candidates.append(sr_support * 0.985)

    # C: 동적 기준%
    gap_pct = dynamic_stop_pct * multiplier
    candidates.append(current_price * (1 - gap_pct / 100))

    if not candidates:
        return current_price * (1 - _MAX_STOP_PCT / 100)

    # median 선택
    stop_loss = statistics.median(candidates)

    # 새벽 보정 (0~6시 KST → 손절 폭 ×1.2)
    hour_kst = (datetime.now(UTC).hour + 9) % 24
    if 0 <= hour_kst < 6:
        gap = current_price - stop_loss
        stop_loss = current_price - gap * 1.2

    return round(stop_loss, 2)


async def enter_defense(
    market: str,
    level: ShockLevel,
    shock_signal: ShockSignal,
    current_price: float,
    stop_loss_price: float,
    pre_shock_bb_width_avg: float,
    pre_shock_volume_avg: float,
) -> DefenseState:
    """방어 모드 진입."""
    state = DefenseState(
        market=market,
        level=level,
        entered_at=datetime.now(UTC),
        shock_signal=shock_signal,
        stop_loss_price=stop_loss_price,
        highest_since_entry=current_price,
        pre_shock_bb_width_avg=pre_shock_bb_width_avg,
        pre_shock_volume_avg=pre_shock_volume_avg,
    )
    r = get_redis()
    await r.hset(f"defense:{market}", mapping=_serialize_state(state))
    await r.sadd("defense:active_set", market)
    logger.warning(
        "defense_mode: ENTER %s level=%s stop=%.2f price=%.2f signals=%s",
        market, level.value, stop_loss_price, current_price,
        shock_signal.triggered_signals,
    )
    return state


async def _save_state(state: DefenseState) -> None:
    """변경된 상태를 Redis에 저장."""
    r = get_redis()
    await r.hset(f"defense:{state.market}", mapping=_serialize_state(state))


async def update_trailing(
    market: str,
    current_price: float,
    current_atr: float | None,
) -> float | None:
    """트레일링 스탑 갱신. 올리기만 가능."""
    state = await get_defense_state(market)
    if state is None:
        return None

    changed = False

    # 최고가 갱신
    if current_price > state.highest_since_entry:
        state.highest_since_entry = current_price
        changed = True

    elapsed = datetime.now(UTC) - state.entered_at
    delay = _TRAILING_DELAY.get(state.level, timedelta(hours=2))

    if elapsed < delay:
        if changed:
            await _save_state(state)
        return state.stop_loss_price  # 고정 기간

    # ATR 기반 트레일링 활성
    if not state.trailing_active:
        state.trailing_active = True
        changed = True
        logger.info("defense_mode: trailing activated %s", market)

    if current_atr and current_atr > 0:
        new_stop = state.highest_since_entry - current_atr * 1.5
        if new_stop > state.stop_loss_price:
            old = state.stop_loss_price
            state.stop_loss_price = round(new_stop, 2)
            changed = True
            logger.info(
                "defense_mode: trailing %s stop %.2f → %.2f",
                market, old, state.stop_loss_price,
            )

    if changed:
        await _save_state(state)
    return state.stop_loss_price


async def check_stop_loss_hit(market: str, current_price: float) -> bool:
    """손절선 이탈 여부 확인."""
    state = await get_defense_state(market)
    if state is None:
        return False
    return current_price <= state.stop_loss_price


async def check_recovery_conditions(
    market: str,
    current_bb_width: float | None,
    current_volume_avg: float | None,
    llm_ok: bool = False,
) -> bool:
    """복귀 조건 확인 (AND). 모든 조건 충족 시 True."""
    state = await get_defense_state(market)
    if state is None:
        return False

    elapsed_hours = (datetime.now(UTC) - state.entered_at).total_seconds() / 3600
    min_hours = _RECOVERY_MIN_HOURS.get(state.level, 4)

    if elapsed_hours < min_hours:
        return False

    # BB 폭 정상화
    if (
        current_bb_width is not None
        and state.pre_shock_bb_width_avg > 0
        and current_bb_width > state.pre_shock_bb_width_avg * 1.5
    ):
        return False

    # 거래량 정상화
    if (
        current_volume_avg is not None
        and state.pre_shock_volume_avg > 0
        and current_volume_avg > state.pre_shock_volume_avg * 2.0
    ):
        return False

    # LLM 재확인
    if not llm_ok:
        return False

    return True


async def exit_defense(market: str) -> DefenseState | None:
    """방어 모드 해제."""
    state = await get_defense_state(market)
    r = get_redis()
    await r.delete(f"defense:{market}")
    await r.srem("defense:active_set", market)
    if state:
        elapsed = (datetime.now(UTC) - state.entered_at).total_seconds() / 60
        logger.info(
            "defense_mode: EXIT %s (%.0f분 경과, level=%s)",
            market, elapsed, state.level.value,
        )
    return state
