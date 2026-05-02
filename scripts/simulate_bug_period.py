"""버그 기간(4/20~4/23) 시뮬레이션 — 현재 전략으로 재평가.

사용법: docker compose exec ct-api python /src/scripts/simulate_bug_period.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# -- ct-api 내부에서 실행하므로 DB 직접 접근 --
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

KST = timezone(timedelta(hours=9))

# ──────────────────────────────────────────────
# 전략 파라미터 (topdown_evaluate.py 기준)
# ───────────────��──────────────────────────────
_GAP_4H = 1.52
_THRESHOLD_BUY = 7.57
_THRESHOLD_SELL = 6.0
_MACD_BLOCK = 50_000

_BOOST_WEIGHT = 0.4
_COUNTER_WEIGHT = 0.2
_MULT_MIN = 0.6
_MULT_MAX = 1.5

MAX_BUY_4H = 18.56
MAX_SELL_4H = 12.2
MAX_BUY_1H = 7.2
MAX_SELL_1H = 2.32

FEE_RATE = 0.0025


# ──────────────────────────────────────────────
# Snap / SR 타입
# ──────────────────────────────────────────────
@dataclass
class Snap:
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    macd_histogram_prev: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    ema_12: float | None = None
    ema_26: float | None = None
    volume_ratio: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class SR:
    levels: list[dict]
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    signal: str = "NONE"
    distance_to_support_pct: float | None = None
    distance_to_resistance_pct: float | None = None


def rebuild_snap(d: dict) -> Snap | None:
    try:
        return Snap(
            rsi_14=_f(d.get("rsi_14")),
            macd=_f(d.get("macd")),
            macd_signal=_f(d.get("macd_signal")),
            macd_histogram=_f(d.get("macd_histogram")),
            macd_histogram_prev=_f(d.get("macd_histogram_prev")),
            bb_upper=_f(d.get("bb_upper")),
            bb_middle=_f(d.get("bb_middle")),
            bb_lower=_f(d.get("bb_lower")),
            sma_20=_f(d.get("sma_20")),
            sma_50=_f(d.get("sma_50")),
            ema_12=_f(d.get("ema_12")),
            ema_26=_f(d.get("ema_26")),
            volume_ratio=_f(d.get("volume_ratio")),
        )
    except Exception:
        return None


def rebuild_sr(d: dict | None) -> SR | None:
    if not d:
        return None
    return SR(
        levels=d.get("levels", []),
        nearest_support=_f(d.get("nearest_support")),
        nearest_resistance=_f(d.get("nearest_resistance")),
        signal=d.get("signal", "NONE"),
        distance_to_support_pct=_f(d.get("distance_to_support_pct")),
        distance_to_resistance_pct=_f(d.get("distance_to_resistance_pct")),
    )


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────
# 지표 체크 함수들 (topdown_tables.py 기준)
# ──────────────────────────────────────────────

# --- 4H BUY ---
def chk_macd_golden(s: Snap, c: float, sr: SR | None) -> bool:
    return (s.macd_histogram or 0) > 0 and (s.macd_histogram_prev or 0) <= 0

def chk_bb_squeeze(s: Snap, c: float, sr: SR | None) -> bool:
    if s.bb_upper and s.bb_lower and s.bb_middle and s.bb_middle > 0:
        return (s.bb_upper - s.bb_lower) / s.bb_middle < 0.03
    return False

def chk_price_gt_sma20(s: Snap, c: float, sr: SR | None) -> bool:
    return s.sma_20 is not None and c > s.sma_20

def chk_macd_hist_rising(s: Snap, c: float, sr: SR | None) -> bool:
    h, p = s.macd_histogram, s.macd_histogram_prev
    return h is not None and p is not None and h > p

def chk_sr_support_near(s: Snap, c: float, sr: SR | None) -> bool:
    if sr and sr.distance_to_support_pct is not None:
        return sr.distance_to_support_pct <= 0.3
    return False

def chk_price_gt_sma50(s: Snap, c: float, sr: SR | None) -> bool:
    return s.sma_50 is not None and c > s.sma_50

def chk_sr_support_bounce(s: Snap, c: float, sr: SR | None) -> bool:
    return sr is not None and sr.signal == "SUPPORT_BOUNCE"

def chk_ema12_gt_ema26(s: Snap, c: float, sr: SR | None) -> bool:
    return s.ema_12 is not None and s.ema_26 is not None and s.ema_12 > s.ema_26

def chk_sr_resist_break(s: Snap, c: float, sr: SR | None) -> bool:
    return sr is not None and sr.signal == "RESISTANCE_BREAK"


BUY_4H = [
    ("macd_골든크로스", 2.45, chk_macd_golden),
    ("bb_squeeze", 2.41, chk_bb_squeeze),
    ("price>sma20", 2.26, chk_price_gt_sma20),
    ("macd_hist_증가중", 2.24, chk_macd_hist_rising),
    ("sr_지지근접", 2.11, chk_sr_support_near),
    ("price>sma50", 2.10, chk_price_gt_sma50),
    ("sr_지지��등", 2.07, chk_sr_support_bounce),
    ("ema12>ema26", 2.03, chk_ema12_gt_ema26),
    ("sr_저항돌파", 1.89, chk_sr_resist_break),
]

# --- 4H SELL ---
def chk_macd_hist_negative(s: Snap, c: float, sr: SR | None) -> bool:
    return (s.macd_histogram or 0) < 0

def chk_rsi_lt40(s: Snap, c: float, sr: SR | None) -> bool:
    return s.rsi_14 is not None and s.rsi_14 < 40

def chk_price_lt_sma50(s: Snap, c: float, sr: SR | None) -> bool:
    return s.sma_50 is not None and c < s.sma_50

def chk_sr_resist_reject(s: Snap, c: float, sr: SR | None) -> bool:
    return sr is not None and sr.signal == "RESISTANCE_REJECT"

def chk_price_lt_sma20(s: Snap, c: float, sr: SR | None) -> bool:
    return s.sma_20 is not None and c < s.sma_20

def chk_macd_hist_falling(s: Snap, c: float, sr: SR | None) -> bool:
    h, p = s.macd_histogram, s.macd_histogram_prev
    return h is not None and p is not None and h < p

def chk_macd_dead(s: Snap, c: float, sr: SR | None) -> bool:
    return (s.macd_histogram or 0) < 0 and (s.macd_histogram_prev or 0) >= 0


SELL_4H = [
    ("macd_hist_음수", 1.73, chk_macd_hist_negative),
    ("rsi<40", 1.71, chk_rsi_lt40),
    ("price<sma50", 1.67, chk_price_lt_sma50),
    ("sr_저항거부", 1.67, chk_sr_resist_reject),
    ("price<sma20", 1.65, chk_price_lt_sma20),
    ("macd_hist_감소중", 1.63, chk_macd_hist_falling),
    ("macd_데드크로스", 1.54, chk_macd_dead),
]

# --- 1H BUY ---
BUY_1H = [
    ("sr_지지반등", 3.36, chk_sr_support_bounce),
    ("sr_저항돌파", 2.84, chk_sr_resist_break),
    ("bb_확장", 0.41, lambda s, c, sr: (
        s.bb_upper and s.bb_lower and s.bb_middle and s.bb_middle > 0
        and (s.bb_upper - s.bb_lower) / s.bb_middle > 0.05
    )),
    ("macd_골든크로스", 0.32, chk_macd_golden),
    ("macd_hist_증가중", 0.27, chk_macd_hist_rising),
]

# --- 1H SELL ---
SELL_1H = [
    ("sr_저항거부", 1.86, chk_sr_resist_reject),
    ("macd_데드크로스", 0.25, chk_macd_dead),
    ("macd_hist_감소중", 0.21, chk_macd_hist_falling),
]

# --- 15m Triggers ---
def chk_rsi_lt50(s: Snap, c: float, sr: SR | None) -> bool:
    return s.rsi_14 is not None and s.rsi_14 < 50

def chk_rsi_lt45(s: Snap, c: float, sr: SR | None) -> bool:
    return s.rsi_14 is not None and s.rsi_14 < 45

def chk_bb_lower_break(s: Snap, c: float, sr: SR | None) -> bool:
    return s.bb_lower is not None and c <= s.bb_lower

def chk_rsi_gt50(s: Snap, c: float, sr: SR | None) -> bool:
    return s.rsi_14 is not None and s.rsi_14 > 50

def chk_rsi_gt55(s: Snap, c: float, sr: SR | None) -> bool:
    return s.rsi_14 is not None and s.rsi_14 > 55

def chk_rsi_gt60(s: Snap, c: float, sr: SR | None) -> bool:
    return s.rsi_14 is not None and s.rsi_14 > 60


TRIGGER_BUY_15M_REQUIRED = [chk_macd_hist_rising]
TRIGGER_BUY_15M_OPTIONAL = [chk_rsi_lt50, chk_rsi_lt45, chk_rsi_lt40, chk_bb_lower_break]
TRIGGER_SELL_15M_REQUIRED = [chk_macd_hist_falling]
TRIGGER_SELL_15M_OPTIONAL = [chk_rsi_gt50, chk_rsi_gt55, chk_rsi_gt60]


# ──────────────────────────────────────────────
# 전략 평가 로직
# ──────────────────────────────────────────────

def evaluate_layer(
    snap: Snap, close: float, sr: SR | None,
    buy_ind: list, sell_ind: list,
) -> tuple[float, float, list[str], list[str], list[str]]:
    buy_score = 0.0
    sell_score = 0.0
    buy_hits: list[str] = []
    sell_hits: list[str] = []
    reasons: list[str] = []
    for name, score, fn in buy_ind:
        if fn(snap, close, sr):
            buy_score += score
            buy_hits.append(name)
            reasons.append(f"BUY {name}")
    for name, score, fn in sell_ind:
        if fn(snap, close, sr):
            sell_score += score
            sell_hits.append(name)
            reasons.append(f"SELL {name}")
    return buy_score, sell_score, reasons, buy_hits, sell_hits


def gate_4h(buy: float, sell: float, macd_h: float | None = None) -> str:
    mh = macd_h or 0
    if mh < -_MACD_BLOCK:
        if sell - buy >= _GAP_4H and sell >= _THRESHOLD_SELL:
            return "SELL"
        return "HOLD"
    if mh > _MACD_BLOCK:
        if buy - sell >= _GAP_4H and buy >= _THRESHOLD_BUY:
            return "BUY"
        return "HOLD"
    if buy - sell >= _GAP_4H and buy >= _THRESHOLD_BUY:
        return "BUY"
    if sell - buy >= _GAP_4H and sell >= _THRESHOLD_SELL:
        return "SELL"
    return "HOLD"


def calc_1h_multiplier(direction: str, buy_1h: float, sell_1h: float) -> float:
    if direction == "BUY":
        boost = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0
        counter = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0
    else:
        boost = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0
        counter = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0
    raw = 1.0 + boost * _BOOST_WEIGHT - counter * _COUNTER_WEIGHT
    return max(_MULT_MIN, min(_MULT_MAX, raw))


def trigger_check_v2(snap: Snap, close: float, req: list, opt: list) -> tuple[bool, list[str]]:
    fired: list[str] = []
    for fn in req:
        if fn(snap, close, None):
            fired.append(fn.__name__)
        else:
            return False, fired
    opt_fired = False
    for fn in opt:
        if fn(snap, close, None):
            fired.append(fn.__name__)
            opt_fired = True
    return opt_fired, fired


def decide_topdown(
    direction: str, score_4h: float, hit_count: int, multiplier: float,
) -> tuple[str, float]:
    if hit_count <= 0:
        return "HOLD", 0.0
    avg_score = score_4h / hit_count
    avg_accuracy = avg_score * 10 + 50
    confidence = (avg_accuracy / 100) * multiplier
    confidence = min(confidence, 1.0)
    return direction, confidence


# ──────────────────────────────────────────────
# DB 조회 + 시뮬레이션
# ──���───────────────────────────────────────────

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-D"]
BUG_START = datetime(2026, 4, 20, 14, 0, tzinfo=KST)
BUG_END = datetime(2026, 4, 23, 20, 5, tzinfo=KST)


async def get_snapshots(session: AsyncSession) -> dict:
    """버그 기간 전체 indicator_snapshots를 메모리에 로드."""
    rows = (await session.execute(text("""
        SELECT market, timeframe,
               candle_open_at AT TIME ZONE 'Asia/Seoul' as open_kst,
               indicators
        FROM indicator_snapshots
        WHERE created_at >= :start AND created_at < :end
          AND market = ANY(:markets)
        ORDER BY candle_open_at
    """), {
        "start": BUG_START, "end": BUG_END,
        "markets": MARKETS,
    })).fetchall()

    # key: (market, timeframe, open_at_rounded_to_tf) → indicators
    data: dict[tuple[str, str, str], dict] = {}
    for market, tf, open_kst, indicators in rows:
        key = (market, tf, str(open_kst)[:16])
        data[key] = indicators
    return data


def find_snapshot(
    data: dict, market: str, tf: str, before: datetime,
) -> dict | None:
    """before 시각 이전의 최신 스냅샷 찾기."""
    before_str = before.strftime("%Y-%m-%d %H:%M")
    best_key = None
    best_time = ""
    for key in data:
        m, t, ts = key
        if m == market and t == tf and ts <= before_str:
            if ts > best_time:
                best_time = ts
                best_key = key
    return data.get(best_key) if best_key else None


def evaluate_at(
    data: dict, market: str, ts: datetime,
) -> dict[str, Any]:
    """특정 시각에 전략 평가 실행."""
    result: dict[str, Any] = {
        "market": market, "time": ts.strftime("%m/%d %H:%M"),
        "signal": "HOLD", "confidence": 0, "reasons": [],
    }

    # 4H
    ind_4h = find_snapshot(data, market, "4h", ts)
    if not ind_4h:
        result["reasons"].append("4H 데이터 없음")
        return result
    snap_4h = rebuild_snap(ind_4h)
    sr_4h = rebuild_sr(ind_4h.get("support_resistance"))
    close_4h = _f(ind_4h.get("close_price")) or 0
    if not snap_4h or close_4h <= 0:
        result["reasons"].append("4H snap 실패")
        return result

    buy_4h, sell_4h, reasons_4h, buy_hits, sell_hits = evaluate_layer(
        snap_4h, close_4h, sr_4h, BUY_4H, SELL_4H,
    )
    result["4h_buy"] = round(buy_4h, 2)
    result["4h_sell"] = round(sell_4h, 2)

    direction = gate_4h(buy_4h, sell_4h, snap_4h.macd_histogram)
    if direction == "HOLD":
        result["reasons"].append(
            f"4H HOLD (B={buy_4h:.1f} S={sell_4h:.1f} MACD_H={snap_4h.macd_histogram or 0:.0f})"
        )
        return result

    hit_count = len(buy_hits) if direction == "BUY" else len(sell_hits)
    score_4h = buy_4h if direction == "BUY" else sell_4h

    # 1H
    ind_1h = find_snapshot(data, market, "1h", ts)
    multiplier = 1.0
    if ind_1h:
        snap_1h = rebuild_snap(ind_1h)
        sr_1h = rebuild_sr(ind_1h.get("support_resistance"))
        close_1h = _f(ind_1h.get("close_price")) or 0
        if snap_1h and close_1h > 0:
            buy_1h, sell_1h, _, _, _ = evaluate_layer(
                snap_1h, close_1h, sr_1h, BUY_1H, SELL_1H,
            )
            multiplier = calc_1h_multiplier(direction, buy_1h, sell_1h)
            result["1h_mult"] = round(multiplier, 2)

    # 15m
    ind_15m = find_snapshot(data, market, "15m", ts)
    if not ind_15m:
        result["reasons"].append(f"4H→{direction} but 15m 데이터 없음")
        return result
    snap_15m = rebuild_snap(ind_15m)
    close_15m = _f(ind_15m.get("close_price")) or 0
    if not snap_15m or close_15m <= 0:
        result["reasons"].append(f"4H→{direction} but 15m snap 실패")
        return result

    if direction == "BUY":
        triggered, fired = trigger_check_v2(
            snap_15m, close_15m, TRIGGER_BUY_15M_REQUIRED, TRIGGER_BUY_15M_OPTIONAL,
        )
    else:
        triggered, fired = trigger_check_v2(
            snap_15m, close_15m, TRIGGER_SELL_15M_REQUIRED, TRIGGER_SELL_15M_OPTIONAL,
        )

    if not triggered:
        result["reasons"].append(
            f"4H→{direction} (mult={multiplier:.2f}) but 15m 트리거 미충족"
        )
        result["15m_rsi"] = snap_15m.rsi_14
        result["15m_macd_h"] = snap_15m.macd_histogram
        return result

    signal, confidence = decide_topdown(direction, score_4h, hit_count, multiplier)
    result["signal"] = signal
    result["confidence"] = round(confidence, 4)
    result["close_price"] = close_15m
    result["reasons"] = [
        f"4H: {','.join(buy_hits if direction == 'BUY' else sell_hits)}",
        f"1H mult={multiplier:.2f}",
        f"15m trigger: {','.join(fired)}",
    ]
    return result


async def get_future_price(
    session: AsyncSession, market: str, after: datetime, hours: int = 4,
) -> float | None:
    """N시간 후 15m close 가격 조회."""
    target = after + timedelta(hours=hours)
    row = (await session.execute(text("""
        SELECT close FROM candles
        WHERE market = :market AND timeframe = '15m'
          AND open_at >= :target
        ORDER BY open_at LIMIT 1
    """), {"market": market, "target": target})).fetchone()
    return float(row[0]) if row else None


async def main():
    import os
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://autocoin:***REMOVED***@localhost:5433/autocoin",
    )
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        print("=== 버그 기간 전략 시뮬레이션 (4/20 14:00 ~ 4/23 20:00 KST) ===\n")
        print("데이터 로딩 중...")

        data = await get_snapshots(session)
        print(f"스냅샷 {len(data)}건 로드 완료\n")

        # 15분 경계마다 평가
        signals: list[dict] = []
        ts = BUG_START
        while ts < BUG_END:
            for market in MARKETS:
                result = evaluate_at(data, market, ts)
                if result["signal"] != "HOLD":
                    signals.append(result)
            ts += timedelta(minutes=15)

        if not signals:
            print("BUY/SELL 시그널: 0건 — 전 구간 HOLD")
            # gate 통과했지만 15m 트리거 미충족 케이스 확인
            print("\n--- 4H 게이트 통과 but 15m 미충족 케이스 (샘플) ---")
            ts = BUG_START
            gate_passed = []
            while ts < BUG_END:
                for market in MARKETS:
                    result = evaluate_at(data, market, ts)
                    reasons = " ".join(result.get("reasons", []))
                    if "4H→" in reasons and "트리거 미충족" in reasons:
                        gate_passed.append(result)
                ts += timedelta(minutes=15)

            for r in gate_passed[:20]:
                coin = r["market"].replace("KRW-", "")
                reasons = " | ".join(r.get("reasons", []))
                rsi = r.get("15m_rsi")
                macd = r.get("15m_macd_h")
                rsi_s = f"RSI={rsi:.1f}" if rsi else ""
                macd_s = f"MACD_H={macd:.0f}" if macd else ""
                print(f"  {r['time']} {coin:4s} | {reasons} | {rsi_s} {macd_s}")

            print(f"\n총 {len(gate_passed)}건 gate 통과 → 15m 미충족")

            await engine.dispose()
            return

        # 시그널 결과 출력
        print(f"{'시간':8s} {'코인':5s} {'판단':4s} {'신뢰':5s} "
              f"{'시그널가':>12s} {'4h후가':>12s} {'변동':>7s} {'결과':3s}")
        print("-" * 70)

        total_pnl = 0.0
        hit = 0
        confirmed = 0

        for s in signals:
            coin = s["market"].replace("KRW-", "")
            price = s.get("close_price", 0)
            conf = s["confidence"]

            # 4h 후 가격
            sig_time = datetime.strptime(
                f"2026/{s['time']}", "%Y/%m/%d %H:%M"
            ).replace(tzinfo=KST)
            fut_price = await get_future_price(session, s["market"], sig_time)

            if fut_price and price > 0:
                chg = (fut_price - price) / price * 100
                if s["signal"] == "BUY":
                    correct = chg > 0
                else:
                    correct = chg < 0
                mark = "✅" if correct else "❌"
                confirmed += 1
                if correct:
                    hit += 1
                # 수수료 반영 수익률
                net_chg = abs(chg) - (FEE_RATE * 100 * 2)  # 매수+매도 수수료
                if not correct:
                    net_chg = -net_chg
                total_pnl += net_chg
            else:
                chg = None
                mark = "⏳"

            chg_str = f"{chg:+.2f}%" if chg is not None else "  -  "
            print(
                f"{s['time']:8s} {coin:5s} {s['signal']:4s} {conf:5.1%} "
                f"{price:>12,.0f} "
                f"{fut_price:>12,.0f} " if fut_price else f"{'':>12s} ",
                end="",
            )
            print(f"{chg_str:>7s} {mark}")

            # 이유 출력
            for reason in s.get("reasons", []):
                print(f"         └ {reason}")

        print("-" * 70)
        print(f"\n총 시그널: {len(signals)}건")
        if confirmed > 0:
            print(f"적중: {hit}/{confirmed} ({hit / confirmed * 100:.0f}%)")
            print(f"누적 수익률 (수수료 포함): {total_pnl:+.2f}%")
            # 100만원 기준 15% 투입 시 추정 손익
            per_trade = 150_000
            est_pnl = sum(
                per_trade * (total_pnl / confirmed / 100)
                for _ in range(confirmed)
            )
            print(f"추정 손익 (100만원, 15% 투입): {total_pnl / 100 * per_trade * confirmed:+,.0f}원")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
