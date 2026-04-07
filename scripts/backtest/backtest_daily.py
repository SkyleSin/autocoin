"""백테스트 — 1년치 일봉 데이터로 시그널 알고리즘 정확도 검증.

사용법: .venv/bin/python backtest_daily.py
"""

from __future__ import annotations

import sys
import time
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
MARKETS = ["KRW-BTC", "KRW-XRP"]
TIMEFRAME = "1d"
WINDOW = 200       # 200일 분석 윈도우
STEP = 1           # 매일 시그널 평가
EVAL_DAYS = [3, 7] # 3일 후, 7일 후 가격 비교
TOTAL_CANDLES = 565 # 1년(365) + 윈도우(200)


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
    """일봉 시그널 평가."""
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
    pat = analyze_patterns(highs, lows, closes, timeframe=TIMEFRAME)
    sr = analyze_sr(highs, lows, closes)
    signal = evaluate(snap, close_price, pattern_analysis=pat, sr_analysis=sr)

    if signal.signal_type == "HOLD":
        return None

    future_price = float(df.iloc[idx + eval_bars]["close"])
    pct_change = (future_price - close_price) / close_price * 100
    hit = pct_change > 0 if signal.signal_type == "BUY" else pct_change < 0

    return {
        "date": str(df.iloc[idx]["open_at"].date()),
        "signal": signal.signal_type,
        "confidence": signal.confidence,
        "reasons": signal.reasons,
        "entry_price": close_price,
        "future_price": future_price,
        "pct_change": round(pct_change, 3),
        "hit": hit,
    }


def print_results(market: str, results: list[dict], eval_days: int):
    if not results:
        print(f"  [{eval_days}일 후 평가] 시그널 없음")
        return

    total = len(results)
    hits = sum(1 for r in results if r["hit"])
    buy_results = [r for r in results if r["signal"] == "BUY"]
    sell_results = [r for r in results if r["signal"] == "SELL"]
    buy_hits = sum(1 for r in buy_results if r["hit"])
    sell_hits = sum(1 for r in sell_results if r["hit"])

    total_return = sum(
        r["pct_change"] if r["signal"] == "BUY" else -r["pct_change"]
        for r in results
    )
    avg_pct = total_return / total

    print(f"\n  ── {eval_days}일 후 평가 ──")
    print(f"  총 시그널: {total}회 (BUY {len(buy_results)} / SELL {len(sell_results)})")
    print(f"  적중률: {hits}/{total} = {hits/total*100:.1f}%")
    if buy_results:
        print(
            f"    BUY 적중률: {buy_hits}/{len(buy_results)}"
            f" = {buy_hits/len(buy_results)*100:.1f}%"
        )
    if sell_results:
        print(
            f"    SELL 적중률: {sell_hits}/{len(sell_results)}"
            f" = {sell_hits/len(sell_results)*100:.1f}%"
        )
    print(f"  평균 수익: {avg_pct:.3f}%")
    print(f"  누적 수익 (단순합산): {total_return:.2f}%")
    print(f"  월 평균 시그널: {total / 12:.1f}회")

    # 최근 15개 시그널
    print(f"\n  📋 최근 시그널 (최대 15개)")
    print(f"  {'날짜':<12} {'시그널':>4} {'진입가':>14} {f'{eval_days}d후':>14}"
          f" {'변동%':>8} {'결과':>4}")
    print(f"  {'─'*62}")
    for r in results[-15:]:
        mark = "✅" if r["hit"] else "❌"
        print(
            f"  {r['date']:<12} {r['signal']:>4}"
            f" {r['entry_price']:>14,.0f} {r['future_price']:>14,.0f}"
            f" {r['pct_change']:>+8.3f} {mark:>4}"
        )

    # 판단 근거별 적중률
    reason_stats: dict[str, dict] = {}
    for r in results:
        for reason in r["reasons"]:
            key = reason.split("(")[0].strip()
            if key not in reason_stats:
                reason_stats[key] = {"total": 0, "hits": 0}
            reason_stats[key]["total"] += 1
            if r["hit"]:
                reason_stats[key]["hits"] += 1

    if reason_stats:
        print(f"\n  📊 판단 근거별 적중률")
        print(f"  {'근거':<25} {'적중':>5} {'전체':>5} {'적중률':>7}")
        print(f"  {'─'*50}")
        for key, stat in sorted(
            reason_stats.items(), key=lambda x: x[1]["total"], reverse=True,
        ):
            rate = stat["hits"] / stat["total"] * 100
            print(f"  {key:<25} {stat['hits']:>5} {stat['total']:>5} {rate:>6.1f}%")


def run_backtest():
    settings = Settings()
    client = BithumbClientBase(settings)

    print(f"=== 백테스트: 1년치 일봉 데이터 ===")
    print(f"구조: 트리거(패턴/지지저항) 필수 + 확인(MACD전환/RSI/BB) + SMA 게이트")
    print(f"분석 윈도우: {WINDOW}일, 평가: {EVAL_DAYS}일 후 가격 비교")
    print(f"패턴 order: 5 (일봉 기본값)\n")

    for market in MARKETS:
        print(f"\n{'='*60}")
        print(f"📊 {market} (일봉 1년)")
        print(f"{'='*60}")

        print(f"  일봉 수집 중... (약 {TOTAL_CANDLES}개)")
        candles = fetch_daily_candles(client, market, TOTAL_CANDLES)
        df = candles_to_df(candles)
        print(f"  수집: {len(df)}일 ({df.iloc[0]['open_at'].date()} ~ {df.iloc[-1]['open_at'].date()})")

        # 기간 확인
        days_span = (df.iloc[-1]["open_at"] - df.iloc[0]["open_at"]).days
        print(f"  기간: {days_span}일 (약 {days_span/30:.1f}개월)")

        for eval_bars in EVAL_DAYS:
            results = []
            for idx in range(WINDOW - 1, len(df) - eval_bars, STEP):
                result = evaluate_signal_at(df, idx, eval_bars)
                if result is not None:
                    results.append(result)
            print_results(market, results, eval_bars)

    client.close()
    print(f"\n{'='*60}")
    print("백테스트 완료")


if __name__ == "__main__":
    run_backtest()
