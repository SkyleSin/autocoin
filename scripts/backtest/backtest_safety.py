"""안전장치 시뮬레이션 — 1분봉으로 지지선 이탈 감지 시 긴급 매도 효과 검증.

15분봉으로 지지선 계산 → 1분봉으로 가격 감시 → 이탈 시 매도 판단 시뮬레이션.

사용법: .venv/bin/python scripts/backtest/backtest_safety.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from bithumb.candle import BithumbCandle  # noqa: E402
from core.analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from utils.config import Settings  # noqa: E402

# ── 설정 ──
MARKETS = ["KRW-BTC", "KRW-XRP", "KRW-SOL"]
DAYS = 30  # 1개월 시뮬레이션
IMMEDIATE_PCT = 5.0  # 즉시 트리거 임계값
CONSECUTIVE_PCT = 1.5  # 연속 감시 임계값
CONSECUTIVE_COUNT = 5  # 연속 이탈 횟수
COOLDOWN_MIN = 5  # 트리거 후 쿨다운 (분)

# 매도 후 N분 뒤 가격 비교로 판단 효과 평가
EVAL_AFTER_MINUTES = [5, 15, 30, 60]


def fetch_candles(
    client: BithumbCandle, market: str, unit: int, total: int,
) -> list[dict]:
    """빗썸에서 캔들 데이터 페이지네이션 수집."""
    all_candles: list[dict] = []
    to_param: str | None = None
    while len(all_candles) < total:
        batch_size = min(200, total - len(all_candles))
        batch = client.get_minutes(unit, market, count=batch_size, to=to_param)
        if not batch:
            break
        all_candles.extend(batch)
        oldest = batch[-1]["candle_date_time_utc"]
        to_param = oldest
        time.sleep(0.12)
    all_candles.sort(key=lambda c: c["candle_date_time_utc"])
    # 중복 제거
    seen = set()
    deduped = []
    for c in all_candles:
        key = c["candle_date_time_utc"]
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


@dataclass
class TriggerEvent:
    """트리거 발생 기록."""
    market: str
    trigger_time: str
    trigger_price: float
    support_level: float
    breach_pct: float
    trigger_type: str  # "immediate" or "consecutive"
    prices_after: dict[int, float] = field(default_factory=dict)  # minutes -> price


def calc_support(candles_15m: list[dict], window: int = 200) -> float | None:
    """15분봉 윈도우에서 지지선 계산."""
    if len(candles_15m) < 30:
        return None
    recent = candles_15m[-window:]
    highs = pd.Series([float(c["high_price"]) for c in recent])
    lows = pd.Series([float(c["low_price"]) for c in recent])
    closes = pd.Series([float(c["trade_price"]) for c in recent])
    sr = analyze_sr(highs, lows, closes)
    return sr.nearest_support


def run_simulation(market: str, candles_1m: list[dict], candles_15m: list[dict]) -> list[TriggerEvent]:
    """1분봉으로 가격 감시, 15분봉으로 지지선 계산."""
    events: list[TriggerEvent] = []
    breach_count = 0
    last_triggered_idx = -COOLDOWN_MIN - 1  # 초기값: 쿨다운 없음

    # 15분봉 인덱스 매핑 (시간 기반)
    support_cache: dict[str, float | None] = {}

    def get_support_at(time_str: str) -> float | None:
        """해당 시점 이전의 15분봉으로 지지선 계산."""
        # 가장 가까운 이전 15분봉까지 사용
        available = [c for c in candles_15m if c["candle_date_time_utc"] <= time_str]
        if len(available) < 30:
            return None
        cache_key = available[-1]["candle_date_time_utc"]
        if cache_key not in support_cache:
            support_cache[cache_key] = calc_support(available)
        return support_cache[cache_key]

    # 1분봉을 시간순 순회
    for i, candle in enumerate(candles_1m):
        price = float(candle["trade_price"])
        time_str = candle["candle_date_time_utc"]

        support = get_support_at(time_str)
        if support is None or support <= 0:
            breach_count = 0
            continue

        breach_pct = (support - price) / support * 100

        # 지지선 위 → 리셋
        if breach_pct < CONSECUTIVE_PCT:
            breach_count = 0
            continue

        # 쿨다운 확인
        if (i - last_triggered_idx) < COOLDOWN_MIN:
            continue

        trigger = False
        trigger_type = ""

        if breach_pct >= IMMEDIATE_PCT:
            trigger = True
            trigger_type = "immediate"
        else:
            breach_count += 1
            if breach_count >= CONSECUTIVE_COUNT:
                trigger = True
                trigger_type = "consecutive"

        if not trigger:
            continue

        # 트리거 발생!
        last_triggered_idx = i
        breach_count = 0

        event = TriggerEvent(
            market=market,
            trigger_time=time_str,
            trigger_price=price,
            support_level=support,
            breach_pct=round(breach_pct, 2),
            trigger_type=trigger_type,
        )

        # 트리거 후 N분 뒤 가격 확인
        for minutes in EVAL_AFTER_MINUTES:
            future_idx = i + minutes
            if future_idx < len(candles_1m):
                event.prices_after[minutes] = float(candles_1m[future_idx]["trade_price"])

        events.append(event)

    return events


def evaluate_events(events: list[TriggerEvent]) -> None:
    """트리거 이벤트 평가 — 매도가 이득이었는지 분석."""
    if not events:
        print("  트리거 없음\n")
        return

    print(f"  총 트리거: {len(events)}회")
    print(f"  즉시(3%+): {sum(1 for e in events if e.trigger_type == 'immediate')}회")
    print(f"  연속(1~3% × 3분): {sum(1 for e in events if e.trigger_type == 'consecutive')}회")
    print()

    # N분 후 매도가 이득이었는지 (가격이 더 떨어졌으면 이득)
    for minutes in EVAL_AFTER_MINUTES:
        correct = 0
        total = 0
        total_saved_pct = 0.0
        for e in events:
            if minutes in e.prices_after:
                total += 1
                later_price = e.prices_after[minutes]
                change_pct = (later_price - e.trigger_price) / e.trigger_price * 100
                if later_price < e.trigger_price:
                    correct += 1  # 더 떨어졌으므로 매도가 옳았음
                total_saved_pct += -change_pct  # 양수면 절약, 음수면 손해

        if total > 0:
            hit_rate = correct / total * 100
            avg_saved = total_saved_pct / total
            print(
                f"  {minutes:>2}분 후 평가: "
                f"적중 {correct}/{total} ({hit_rate:.0f}%) | "
                f"평균 {'절약' if avg_saved >= 0 else '손해'} {abs(avg_saved):.2f}%"
            )

    print()

    # 개별 이벤트 표시 (최대 15개)
    print("  ── 트리거 상세 (최근 15건) ──")
    for e in events[-15:]:
        after_5m = e.prices_after.get(5)
        after_30m = e.prices_after.get(30)
        after_5m_str = f"{(after_5m - e.trigger_price) / e.trigger_price * 100:+.2f}%" if after_5m else "N/A"
        after_30m_str = f"{(after_30m - e.trigger_price) / e.trigger_price * 100:+.2f}%" if after_30m else "N/A"
        print(
            f"  {e.trigger_time} | {e.trigger_type:>11} | "
            f"이탈 {e.breach_pct:.1f}% | "
            f"가격 {e.trigger_price:>14,.0f} | "
            f"5분후 {after_5m_str:>7} | 30분후 {after_30m_str:>7}"
        )
    print()


def main():
    settings = Settings()
    client = BithumbCandle(settings)

    total_1m = DAYS * 24 * 60  # 1분봉 총 개수
    total_15m = DAYS * 24 * 4 + 200  # 15분봉 (분석 윈도우 포함)

    for market in MARKETS:
        print(f"{'=' * 80}")
        print(f"  {market} — {DAYS}일 안전장치 시뮬레이션")
        print(f"  즉시 트리거: {IMMEDIATE_PCT}%+ | 연속 트리거: {CONSECUTIVE_PCT}~{IMMEDIATE_PCT}% × {CONSECUTIVE_COUNT}분")
        print(f"{'=' * 80}")

        # 1분봉 수집
        print(f"  1분봉 수집 중 ({total_1m}개)...", end="", flush=True)
        candles_1m = fetch_candles(client, market, 1, total_1m)
        print(f" {len(candles_1m)}개 수집")

        # 15분봉 수집
        print(f"  15분봉 수집 중 ({total_15m}개)...", end="", flush=True)
        candles_15m = fetch_candles(client, market, 15, total_15m)
        print(f" {len(candles_15m)}개 수집")

        # 시뮬레이션 실행
        print("  시뮬레이션 실행 중...")
        events = run_simulation(market, candles_1m, candles_15m)

        # 결과 평가
        evaluate_events(events)


if __name__ == "__main__":
    main()
