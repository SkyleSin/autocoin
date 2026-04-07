"""백테스트 — 4주치 15분봉 데이터로 시그널 알고리즘 정확도 검증.

사용법: .venv/bin/python backtest.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

# src를 import path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from analysis.indicators import compute_all, sma  # noqa: E402
from analysis.patterns import analyze as analyze_patterns  # noqa: E402
from analysis.signal_generator import evaluate  # noqa: E402
from analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from bithumb.candle import BithumbCandle  # noqa: E402
from utils.config import Settings  # noqa: E402

# ── 설정 ──
MARKETS = ["KRW-BTC", "KRW-XRP"]
UNIT = 15  # 15분봉
TIMEFRAME = "15m"
WINDOW = 200  # 분석 윈도우 (캔들 수)
STEP = 4  # 4캔들(=1시간)마다 시그널 평가
EVAL_BARS = 16  # 시그널 후 16캔들(=4시간) 뒤 가격 비교
WEEKS = 4
REF_TF_UNIT = 60  # 1시간봉 (상위 추세 참조)
REF_TF_LABEL = "1h"


def fetch_candles(
    client: BithumbCandle, market: str, unit: int, total: int,
) -> list[dict]:
    """빗썸에서 캔들 데이터를 페이지네이션으로 수집."""
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


def get_higher_trend(ref_df: pd.DataFrame, current_time) -> str:
    """상위 타임프레임 추세 판단."""
    ref_before = ref_df[ref_df["open_at"] <= current_time]
    if len(ref_before) < 50:
        return "NEUTRAL"
    ref_closes = pd.Series(ref_before["close"].values[-60:])
    sma_20 = sma(ref_closes, 20)
    sma_50 = sma(ref_closes, 50)
    last_20, last_50 = float(sma_20.iloc[-1]), float(sma_50.iloc[-1])
    recent = sma_20.iloc[-3:]
    slope = (float(recent.iloc[-1]) - float(recent.iloc[0])) / float(recent.iloc[0]) * 100
    if last_20 > last_50 and slope > 0:
        return "UP"
    if last_20 < last_50 and slope < 0:
        return "DOWN"
    return "NEUTRAL"


def evaluate_signal_at(
    df: pd.DataFrame, idx: int, ref_df: pd.DataFrame | None = None,
) -> dict | None:
    """df[idx-WINDOW+1:idx+1] 구간으로 시그널 평가."""
    start = idx - WINDOW + 1
    if start < 0 or idx + EVAL_BARS >= len(df):
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

    # 상위 추세 필터링 (MTF)
    higher_trend = "NEUTRAL"
    if ref_df is not None:
        current_time = df.iloc[idx]["open_at"]
        higher_trend = get_higher_trend(ref_df, current_time)
        if signal.signal_type == "BUY" and higher_trend == "DOWN":
            return {
                "signal": signal.signal_type,
                "filtered": True,
                "reason": "상위 추세 하락 → 매수 보류",
            }

    # 미래 가격 비교
    future_price = float(df.iloc[idx + EVAL_BARS]["close"])
    pct_change = (future_price - close_price) / close_price * 100
    hit = pct_change > 0 if signal.signal_type == "BUY" else pct_change < 0

    return {
        "time": str(df.iloc[idx]["open_at"]),
        "signal": signal.signal_type,
        "confidence": signal.confidence,
        "reasons": signal.reasons,
        "entry_price": close_price,
        "future_price": future_price,
        "pct_change": round(pct_change, 3),
        "hit": hit,
        "higher_trend": higher_trend,
        "filtered": False,
    }


def print_results(market: str, results: list[dict], filtered_count: int):
    """결과 출력."""
    if not results:
        print("  시그널 없음")
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

    print(f"\n  📈 결과 요약")
    print(f"  {'─'*50}")
    print(f"  총 시그널: {total}회 (BUY {len(buy_results)} / SELL {len(sell_results)})")
    print(f"  MTF 필터로 차단: {filtered_count}회")
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
    print(f"  하루 평균 시그널: {total / (WEEKS * 7):.1f}회")

    # 최근 10개 시그널
    print(f"\n  📋 최근 시그널 (최대 10개)")
    print(
        f"  {'시각':<22} {'시그널':>4} {'진입가':>12} {'4h후':>12}"
        f" {'변동%':>7} {'결과':>4} {'추세':>7}"
    )
    print(f"  {'─'*78}")
    for r in results[-10:]:
        mark = "✅" if r["hit"] else "❌"
        trend = r.get("higher_trend", "N/A")
        print(
            f"  {r['time'][:19]:<22} {r['signal']:>4}"
            f" {r['entry_price']:>12,.0f} {r['future_price']:>12,.0f}"
            f" {r['pct_change']:>+7.3f} {mark:>4} {trend:>7}"
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
    client = BithumbCandle(settings)

    total_15m = WEEKS * 7 * 24 * 4 + WINDOW
    total_1h = WEEKS * 7 * 24 + 60

    print(f"=== 백테스트: {WEEKS}주치 {TIMEFRAME} 데이터 ===")
    print("구조: 트리거(패턴/지지저항) 필수 + 확인(MACD전환/RSI/BB) + SMA 게이트")
    print(f"분석 윈도우: {WINDOW}캔들, 평가: {EVAL_BARS}캔들 ({EVAL_BARS * 15}분)")
    print(f"상위 추세 필터: {REF_TF_LABEL} SMA20/50\n")

    for market in MARKETS:
        print(f"\n{'='*60}")
        print(f"📊 {market}")
        print(f"{'='*60}")

        print(f"  15분봉 수집 중... (약 {total_15m}개)")
        candles_15m = fetch_candles(client, market, UNIT, total_15m)
        df = candles_to_df(candles_15m)
        print(f"  수집: {len(df)}개 ({df.iloc[0]['open_at']} ~ {df.iloc[-1]['open_at']})")

        print(f"  1시간봉 수집 중...")
        candles_1h = fetch_candles(client, market, REF_TF_UNIT, total_1h)
        ref_df = candles_to_df(candles_1h)
        print(f"  수집: {len(ref_df)}개")

        results = []
        filtered_count = 0

        for idx in range(WINDOW - 1, len(df) - EVAL_BARS, STEP):
            result = evaluate_signal_at(df, idx, ref_df)
            if result is None:
                continue
            if result.get("filtered"):
                filtered_count += 1
                continue
            results.append(result)

        print_results(market, results, filtered_count)

    client.close()
    print(f"\n{'='*60}")
    print("백테스트 완료")


if __name__ == "__main__":
    run_backtest()
