"""탑다운 MTF 백테스트 — 적중률 스코어 + 시그널 전환 평가."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _topdown_fetch import (  # noqa: E402
    candles_to_df,
    compute_at,
    fetch_candles_minutes,
    find_tf_idx,
)
from _topdown_report import print_indicator_stats, print_strategy_results  # noqa: E402
from _topdown_scores import compute_signal  # noqa: E402
from _topdown_tracker import PositionTracker  # noqa: E402

from bithumb.candle import BithumbCandle  # noqa: E402
from utils.config import Settings  # noqa: E402

MARKETS = ["KRW-BTC", "KRW-ETH"]
MONTHS = 12
WINDOW = 200
STEP = 4  # 15분봉 4개 = 1시간마다 평가


def run_backtest():
    settings = Settings()
    client = BithumbCandle(settings)
    tracker = PositionTracker()

    bars_15m = MONTHS * 30 * 24 * 4 + WINDOW
    bars_1h = MONTHS * 30 * 24 + WINDOW
    bars_4h = MONTHS * 30 * 6 + WINDOW

    print("=" * 70)
    print(f"  탑다운 MTF 백테스트 | {MONTHS}개월 | {', '.join(MARKETS)}")
    print("=" * 70)

    for market in MARKETS:
        print(f"\n{'─' * 60}\n  {market} 수집 중...")
        dfs = {}
        for unit, label, bars in [(15, "15m", bars_15m), (60, "1h", bars_1h),
                                   (240, "4h", bars_4h)]:
            dfs[label] = candles_to_df(fetch_candles_minutes(client, market, unit, bars))
            print(f"  {label}: {len(dfs[label])}개")
        if len(dfs["15m"]) < WINDOW + 100:
            print("  데이터 부족, 건너뜀")
            continue
        _evaluate_market(market, dfs["15m"], dfs["1h"], dfs["4h"], tracker)

    client.close()
    print_indicator_stats(tracker.trades)
    print_strategy_results(tracker.trades, MONTHS)


def _evaluate_market(
    market: str, df_15m: pd.DataFrame,
    df_1h: pd.DataFrame, df_4h: pd.DataFrame,
    tracker: PositionTracker,
):
    """단일 마켓 시그널 전환 평가 루프."""
    count = 0
    for idx in range(WINDOW - 1, len(df_15m) - 1, STEP):
        count += 1
        t = df_15m.iloc[idx]["open_at"]

        for si in range(max(0, idx - STEP + 1), idx + 1):
            tracker.update_all(
                market, float(df_15m.iloc[si]["high"]),
                float(df_15m.iloc[si]["low"]),
            )

        snap_15m, sr_15m, c15 = compute_at(df_15m, idx)
        if snap_15m is None:
            continue

        idx_1h = find_tf_idx(df_1h, t)
        s1h, sr1h, c1h = (None, None, 0.0)
        if idx_1h and idx_1h >= 26:
            s1h, sr1h, c1h = compute_at(df_1h, idx_1h)

        idx_4h = find_tf_idx(df_4h, t)
        s4h, sr4h, c4h = (None, None, 0.0)
        if idx_4h and idx_4h >= 26:
            s4h, sr4h, c4h = compute_at(df_4h, idx_4h)

        sig = compute_signal(s4h, sr4h, c4h, s1h, sr1h, c1h, snap_15m, c15)
        if sig.direction != "HOLD":
            score = sig.buy_score if sig.direction == "BUY" else sig.sell_score
            tracker.on_signal(
                market, sig.direction, c15, str(t), score, sig.fired_indicators,
                confidence=sig.confidence,
            )

        if count % 2000 == 0:
            print(f"    {market} 진행: {count}회...")

    print(f"  {market} 완료: {count}회, {len(tracker.trades)}건 트레이드")


if __name__ == "__main__":
    run_backtest()
