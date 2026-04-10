"""패턴별·코인별 적중률 비교 백테스트 — 2년치 일봉.

사용법: docker compose exec ct-api python scripts/backtest/backtest_pattern_accuracy.py
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from analysis.indicators import compute_all  # noqa: E402
from analysis.patterns import analyze as analyze_patterns  # noqa: E402
from analysis.signal_generator import evaluate  # noqa: E402
from analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from bithumb.base import BithumbClientBase  # noqa: E402
from utils.config import Settings  # noqa: E402

# ── 설정 ──
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
TIMEFRAME = "1d"
WINDOW = 200
STEP = 1
EVAL_DAYS = 7
TOTAL_CANDLES = 930  # 2년(730) + 윈도우(200)


def fetch_daily_candles(client: BithumbClientBase, market: str, total: int) -> list[dict]:
    """일봉 캔들 페이지네이션 수집."""
    all_candles: list[dict] = []
    to_param: str | None = None

    while len(all_candles) < total:
        batch_size = min(200, total - len(all_candles))
        params: dict = {"market": market, "count": batch_size}
        if to_param:
            params["to"] = to_param
        batch = client.public_request("GET", "/v1/candles/days", params=params)
        if not isinstance(batch, list) or not batch:
            break
        all_candles.extend(batch)
        to_param = batch[-1]["candle_date_time_utc"]
        time.sleep(0.15)

    all_candles.sort(key=lambda c: c["candle_date_time_utc"])
    seen = set()
    unique = []
    for c in all_candles:
        key = (c["market"], c["candle_date_time_utc"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["open_at"] = pd.to_datetime(df["candle_date_time_utc"], utc=True)
    df = df.sort_values("open_at").reset_index(drop=True)
    df["open"] = df["opening_price"].astype(float)
    df["high"] = df["high_price"].astype(float)
    df["low"] = df["low_price"].astype(float)
    df["close"] = df["trade_price"].astype(float)
    df["volume"] = df["candle_acc_trade_volume"].astype(float)
    return df


def evaluate_signal_at(df: pd.DataFrame, idx: int, eval_bars: int) -> dict | None:
    """일봉 시그널 평가 — 패턴명 포함."""
    start = idx - WINDOW + 1
    if start < 0 or idx + eval_bars >= len(df):
        return None

    window = df.iloc[start:idx + 1]
    highs = pd.Series(window["high"].values)
    lows = pd.Series(window["low"].values)
    closes = pd.Series(window["close"].values)
    volumes = pd.Series(window["volume"].values)

    snap = compute_all(closes, volumes)
    if snap is None:
        return None

    close_price = float(closes.iloc[-1])
    pat = analyze_patterns(highs, lows, closes, timeframe=TIMEFRAME, volumes=volumes)
    sr = analyze_sr(highs, lows, closes)
    signal = evaluate(snap, close_price, pattern_analysis=pat, sr_analysis=sr)

    if signal.signal_type == "HOLD":
        return None

    # 패턴명 추출
    pattern_name = ""
    pattern_name_ko = ""
    if pat and pat.patterns:
        pattern_name = pat.patterns[0].name
        pattern_name_ko = pat.patterns[0].name_ko

    future_price = float(df.iloc[idx + eval_bars]["close"])
    pct_change = (future_price - close_price) / close_price * 100
    hit = pct_change > 0 if signal.signal_type == "BUY" else pct_change < 0

    return {
        "date": str(df.iloc[idx]["open_at"].date()),
        "signal": signal.signal_type,
        "confidence": signal.confidence,
        "pattern": pattern_name,
        "pattern_ko": pattern_name_ko,
        "entry_price": close_price,
        "future_price": future_price,
        "pct_change": round(pct_change, 3),
        "hit": hit,
    }


def run_backtest():
    settings = Settings()
    client = BithumbClientBase(settings)

    print(f"=== 패턴별·코인별 적중률 비교 백테스트 ===")
    print(f"코인: {', '.join(MARKETS)}")
    print(f"기간: 2년치 일봉, 평가: {EVAL_DAYS}일 후, 윈도우: {WINDOW}일\n")

    # {market: [results]}
    all_results: dict[str, list[dict]] = {}
    # {pattern: {market: {total, hit}}}
    pattern_by_coin: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"total": 0, "hit": 0}))
    # {pattern: {total, hit, signal}}
    pattern_global: dict[str, dict] = defaultdict(lambda: {"total": 0, "hit": 0, "signal": ""})
    # {market: {total, hit}}
    coin_global: dict[str, dict] = defaultdict(lambda: {"total": 0, "hit": 0})

    for market in MARKETS:
        coin = market.replace("KRW-", "")
        print(f"📊 {market} 수집 중...", end=" ", flush=True)
        candles = fetch_daily_candles(client, market, TOTAL_CANDLES)
        df = candles_to_df(candles)
        days_span = (df.iloc[-1]["open_at"] - df.iloc[0]["open_at"]).days
        print(f"{len(df)}일 ({days_span // 30}개월)")

        results = []
        for idx in range(WINDOW - 1, len(df) - EVAL_DAYS, STEP):
            result = evaluate_signal_at(df, idx, EVAL_DAYS)
            if result is not None:
                results.append(result)

        all_results[market] = results

        # 집계
        for r in results:
            pat = r["pattern_ko"] or r["pattern"] or "패턴없음"
            pattern_by_coin[pat][coin]["total"] += 1
            if r["hit"]:
                pattern_by_coin[pat][coin]["hit"] += 1

            pattern_global[pat]["total"] += 1
            pattern_global[pat]["signal"] = r["signal"]
            if r["hit"]:
                pattern_global[pat]["hit"] += 1

            coin_global[coin]["total"] += 1
            if r["hit"]:
                coin_global[coin]["hit"] += 1

    client.close()

    # ── 1. 코인별 전체 적중률 ──
    print(f"\n{'='*60}")
    print("📈 1. 코인별 전체 적중률")
    print(f"{'='*60}")
    print(f"{'코인':<6} {'시그널':>6} {'적중':>6} {'적중률':>7} {'BUY':>10} {'SELL':>10}")
    print("-" * 50)
    for market in MARKETS:
        coin = market.replace("KRW-", "")
        stats = coin_global[coin]
        rate = stats["hit"] / stats["total"] * 100 if stats["total"] > 0 else 0
        results = all_results[market]
        buy_r = [r for r in results if r["signal"] == "BUY"]
        sell_r = [r for r in results if r["signal"] == "SELL"]
        buy_hit = sum(1 for r in buy_r if r["hit"])
        sell_hit = sum(1 for r in sell_r if r["hit"])
        buy_str = f"{buy_hit}/{len(buy_r)}" if buy_r else "-"
        sell_str = f"{sell_hit}/{len(sell_r)}" if sell_r else "-"
        print(
            f"{coin:<6} {stats['total']:>6} {stats['hit']:>6}"
            f" {rate:>6.1f}% {buy_str:>10} {sell_str:>10}"
        )

    # ── 2. 패턴별 전체 적중률 ──
    print(f"\n{'='*60}")
    print("🎯 2. 패턴별 전체 적중률 (모든 코인 합산)")
    print(f"{'='*60}")
    print(f"{'패턴':<8} {'방향':<5} {'시그널':>6} {'적중':>6} {'적중률':>7}")
    print("-" * 38)
    for pat, stats in sorted(pattern_global.items(), key=lambda x: x[1]["total"], reverse=True):
        rate = stats["hit"] / stats["total"] * 100 if stats["total"] > 0 else 0
        print(f"{pat:<8} {stats['signal']:<5} {stats['total']:>6} {stats['hit']:>6} {rate:>6.1f}%")

    # ── 3. 패턴별·코인별 적중률 매트릭스 ──
    print(f"\n{'='*60}")
    print("📐 3. 패턴별·코인별 적중률 매트릭스")
    print(f"{'='*60}")
    coins = [m.replace("KRW-", "") for m in MARKETS]
    header = f"{'패턴':<8}" + "".join(f"{c:>12}" for c in coins) + f"{'전체':>12}"
    print(header)
    print("-" * (8 + 12 * (len(coins) + 1)))

    for pat, by_coin in sorted(pattern_by_coin.items(), key=lambda x: pattern_global[x[0]]["total"], reverse=True):
        row = f"{pat:<8}"
        for coin in coins:
            s = by_coin.get(coin, {"total": 0, "hit": 0})
            if s["total"] > 0:
                rate = s["hit"] / s["total"] * 100
                row += f"{rate:>5.0f}%({s['total']:>3})"
            else:
                row += f"{'---':>12}"
        # 전체
        gs = pattern_global[pat]
        g_rate = gs["hit"] / gs["total"] * 100 if gs["total"] > 0 else 0
        row += f"{g_rate:>5.0f}%({gs['total']:>3})"
        print(row)

    # ── 4. 코인별 패턴 적합성 판단 ──
    print(f"\n{'='*60}")
    print("💡 4. 코인별 패턴 적합성 판단")
    print(f"{'='*60}")
    for coin in coins:
        stats = coin_global[coin]
        if stats["total"] == 0:
            print(f"  {coin}: 시그널 없음")
            continue
        rate = stats["hit"] / stats["total"] * 100
        if rate >= 50:
            verdict = "✅ 적합"
        elif rate >= 40:
            verdict = "⚠️ 보통"
        else:
            verdict = "❌ 부적합"
        print(f"  {coin}: {rate:.1f}% ({stats['total']}건) → {verdict}")

    print(f"\n{'='*60}")
    print("백테스트 완료")


if __name__ == "__main__":
    run_backtest()
