"""지표별 적중률 백테스트 — 멀티 타임프레임 × 멀티 코인 1년치.

사용법: .venv/bin/python scripts/backtest/backtest_weights.py
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from analysis.indicators import compute_all, sma  # noqa: E402
from analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from bithumb.candle import BithumbCandle  # noqa: E402
from utils.config import Settings  # noqa: E402

# ── 설정 ──
MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
WEEKS = 52  # 1년

# 타임프레임별 설정
TF_CONFIGS = {
    "5m": {
        "unit": 5,
        "window": 200,
        "step": 3,          # 15분마다 평가
        "eval_short": 12,   # 1시간 후
        "eval_long": 24,    # 2시간 후
        "eval_label_short": "1h",
        "eval_label_long": "2h",
        "ref_unit": 60,     # 상위 TF: 1시간
        "ref_label": "1h",
    },
    "15m": {
        "unit": 15,
        "window": 200,
        "step": 4,          # 1시간마다 평가
        "eval_short": 16,   # 4시간 후
        "eval_long": 32,    # 8시간 후
        "eval_label_short": "4h",
        "eval_label_long": "8h",
        "ref_unit": 240,    # 상위 TF: 4시간
        "ref_label": "4h",
    },
    "1h": {
        "unit": 60,
        "window": 200,
        "step": 1,          # 매 봉 평가
        "eval_short": 4,    # 4시간 후
        "eval_long": 24,    # 24시간 후
        "eval_label_short": "4h",
        "eval_label_long": "24h",
        "ref_unit": 240,    # 상위 TF: 4시간
        "ref_label": "4h",
    },
    "1d": {
        "unit": 1440,       # 빗썸 일봉
        "window": 120,      # 120일 윈도우
        "step": 1,
        "eval_short": 3,    # 3일 후
        "eval_long": 7,     # 7일 후
        "eval_label_short": "3d",
        "eval_label_long": "7d",
        "ref_unit": None,   # 상위 TF 없음
        "ref_label": None,
    },
}

SR_SIGNALS = {
    "SUPPORT_BOUNCE": "BUY",
    "SUPPORT_BREAK": "SELL",
    "RESISTANCE_BREAK": "BUY",
    "RESISTANCE_REJECT": "SELL",
}

VOLUME_CONFIRM_RATIO = 1.5


def fetch_candles(
    client: BithumbCandle, market: str, unit: int, total: int,
) -> list[dict]:
    """빗썸에서 캔들 데이터를 페이지네이션으로 수집."""
    all_candles: list[dict] = []
    to_param: str | None = None

    while len(all_candles) < total:
        batch_size = min(200, total - len(all_candles))
        try:
            if unit == 1440:
                batch = client.get_days(market, count=batch_size, to=to_param)
            else:
                batch = client.get_minutes(unit, market, count=batch_size, to=to_param)
        except Exception as e:
            print(f"    API 오류: {e}, 재시도...")
            time.sleep(1)
            continue
        if not batch:
            break
        all_candles.extend(batch)
        oldest = batch[-1]["candle_date_time_utc"]
        to_param = oldest
        time.sleep(0.12)

    all_candles.sort(key=lambda c: c["candle_date_time_utc"])
    seen = set()
    unique = []
    for c in all_candles:
        key = (c.get("market", market), c["candle_date_time_utc"])
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


def analyze_at(
    df: pd.DataFrame, idx: int, cfg: dict,
    ref_df: pd.DataFrame | None = None,
) -> dict | None:
    """각 캔들 시점에서 S/R + 지표 상태를 분해하여 기록."""
    window_size = cfg["window"]
    eval_long = cfg["eval_long"]
    start = idx - window_size + 1
    if start < 0 or idx + eval_long >= len(df):
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

    sr = analyze_sr(highs, lows, closes)
    if sr.signal == "NONE":
        return None

    sr_direction = SR_SIGNALS.get(sr.signal)
    if not sr_direction:
        return None

    vol_ratio = snap.volume_ratio or 0.0
    has_volume = vol_ratio >= VOLUME_CONFIRM_RATIO

    if sr.signal in ("SUPPORT_BREAK", "RESISTANCE_BREAK") and not has_volume:
        return None

    # 지표 상태
    indicators_active: dict[str, str] = {}

    if snap.rsi_14 is not None:
        if snap.rsi_14 < 30:
            indicators_active["rsi_oversold"] = "BUY"
        elif snap.rsi_14 > 70:
            indicators_active["rsi_overbought"] = "SELL"

    if snap.macd_histogram is not None and snap.macd_histogram_prev is not None:
        if snap.macd_histogram > 0 and snap.macd_histogram_prev <= 0:
            indicators_active["macd_golden"] = "BUY"
        elif snap.macd_histogram < 0 and snap.macd_histogram_prev >= 0:
            indicators_active["macd_dead"] = "SELL"

    if snap.bb_lower is not None and snap.bb_upper is not None:
        if close_price <= snap.bb_lower:
            indicators_active["bb_lower"] = "BUY"
        elif close_price >= snap.bb_upper:
            indicators_active["bb_upper"] = "SELL"

    # SMA 게이트
    sma_gate = "NEUTRAL"
    if snap.sma_20 is not None and snap.sma_50 is not None:
        if snap.sma_20 > snap.sma_50:
            sma_gate = "BULLISH"
        elif snap.sma_20 < snap.sma_50:
            sma_gate = "BEARISH"

    # 상위 추세
    higher_trend = "NEUTRAL"
    if ref_df is not None:
        current_time = df.iloc[idx]["open_at"]
        higher_trend = get_higher_trend(ref_df, current_time)

    # 미래 가격
    eval_short = cfg["eval_short"]
    price_short = float(df.iloc[idx + eval_short]["close"])
    price_long = float(df.iloc[idx + eval_long]["close"])
    chg_short = (price_short - close_price) / close_price * 100
    chg_long = (price_long - close_price) / close_price * 100

    return {
        "time": str(df.iloc[idx]["open_at"]),
        "close": close_price,
        "sr_signal": sr.signal,
        "sr_direction": sr_direction,
        "volume_ratio": round(vol_ratio, 2),
        "has_volume": has_volume,
        "indicators": indicators_active,
        "sma_gate": sma_gate,
        "higher_trend": higher_trend,
        "rsi": round(snap.rsi_14, 1) if snap.rsi_14 else None,
        "macd_h": round(snap.macd_histogram, 0) if snap.macd_histogram else None,
        "chg_short": round(chg_short, 3),
        "chg_long": round(chg_long, 3),
    }


def compute_hit_rates(results: list[dict]) -> dict:
    """개별 지표 및 조합별 적중률 산출."""
    sr_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "hit_s": 0, "hit_l": 0, "chg_s": 0.0, "chg_l": 0.0,
    })
    ind_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "hit_s": 0, "hit_l": 0, "chg_s": 0.0, "chg_l": 0.0,
    })
    combo_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "hit_s": 0, "hit_l": 0, "chg_s": 0.0, "chg_l": 0.0,
    })
    sma_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "hit_s": 0, "hit_l": 0,
    })
    trend_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "hit_s": 0, "hit_l": 0,
    })

    for r in results:
        direction = r["sr_direction"]
        chg_s = r["chg_short"]
        chg_l = r["chg_long"]

        if direction == "BUY":
            hit_s, hit_l = chg_s > 0, chg_l > 0
            signed_s, signed_l = chg_s, chg_l
        else:
            hit_s, hit_l = chg_s < 0, chg_l < 0
            signed_s, signed_l = -chg_s, -chg_l

        # S/R
        sk = r["sr_signal"]
        sr_stats[sk]["total"] += 1
        sr_stats[sk]["hit_s"] += int(hit_s)
        sr_stats[sk]["hit_l"] += int(hit_l)
        sr_stats[sk]["chg_s"] += signed_s
        sr_stats[sk]["chg_l"] += signed_l

        # 지표
        for ind_name, ind_dir in r["indicators"].items():
            aligned = ind_dir == direction
            ind_stats[ind_name]["total"] += 1
            if aligned:
                ind_stats[ind_name]["hit_s"] += int(hit_s)
                ind_stats[ind_name]["hit_l"] += int(hit_l)
                ind_stats[ind_name]["chg_s"] += signed_s
                ind_stats[ind_name]["chg_l"] += signed_l
            else:
                ind_stats[ind_name]["hit_s"] += int(not hit_s)
                ind_stats[ind_name]["hit_l"] += int(not hit_l)
                ind_stats[ind_name]["chg_s"] -= signed_s
                ind_stats[ind_name]["chg_l"] -= signed_l

        # 조합
        aligned_inds = [n for n, d in r["indicators"].items() if d == direction]
        if aligned_inds:
            ck = f"{sk} + {'+'.join(sorted(aligned_inds))}"
            combo_stats[ck]["total"] += 1
            combo_stats[ck]["hit_s"] += int(hit_s)
            combo_stats[ck]["hit_l"] += int(hit_l)
            combo_stats[ck]["chg_s"] += signed_s
            combo_stats[ck]["chg_l"] += signed_l

        # SMA
        sma_label = f"{direction}+SMA_{r['sma_gate']}"
        sma_stats[sma_label]["total"] += 1
        sma_stats[sma_label]["hit_s"] += int(hit_s)
        sma_stats[sma_label]["hit_l"] += int(hit_l)

        # 추세
        trend_label = f"{direction}+TREND_{r['higher_trend']}"
        trend_stats[trend_label]["total"] += 1
        trend_stats[trend_label]["hit_s"] += int(hit_s)
        trend_stats[trend_label]["hit_l"] += int(hit_l)

    return {
        "sr": dict(sr_stats),
        "indicator": dict(ind_stats),
        "combo": dict(combo_stats),
        "sma_gate": dict(sma_stats),
        "trend": dict(trend_stats),
    }


def print_stats_table(
    title: str, stats: dict, label_s: str, label_l: str,
    min_samples: int = 5, show_avg: bool = True,
):
    print(f"\n  {title}")
    print(f"  {'─'*90}")
    hdr = (
        f"  {'항목':<40} {'건수':>5}"
        f" {label_s+'적중':>7} {label_s+'률':>6}"
        f" {label_l+'적중':>7} {label_l+'률':>6}"
    )
    if show_avg:
        hdr += f"  {'평균수익':>14}"
    print(hdr)
    print(f"  {'─'*90}")

    for key, s in sorted(stats.items(), key=lambda x: x[1]["total"], reverse=True):
        if s["total"] < min_samples:
            continue
        rate_s = s["hit_s"] / s["total"] * 100
        rate_l = s["hit_l"] / s["total"] * 100
        line = (
            f"  {key:<40} {s['total']:>5}"
            f" {s['hit_s']:>5}  {rate_s:>5.1f}%"
            f" {s['hit_l']:>5}  {rate_l:>5.1f}%"
        )
        if show_avg and "chg_s" in s:
            avg_s = s["chg_s"] / s["total"]
            avg_l = s["chg_l"] / s["total"]
            line += f"  ({avg_s:+.3f}%/{avg_l:+.3f}%)"
        print(line)


def suggest_weights(stats: dict, label_l: str):
    print(f"\n  {'='*60}")
    print(f"  💡 제안 base 가중치 ({label_l} 적중률 기반)")
    print(f"  {'='*60}")

    print(f"\n  [S/R 트리거]")
    for key, s in sorted(stats["sr"].items(), key=lambda x: x[1]["total"], reverse=True):
        if s["total"] < 10:
            continue
        rate = s["hit_l"] / s["total"]
        weight = max(0.20, min(0.50, rate * 0.7))
        print(f"    {key:<25} 적중률 {rate*100:5.1f}% ({s['total']:>4}건) → 가중치 {weight:.2f}")

    print(f"\n  [확인 지표]")
    for key, s in sorted(
        stats["indicator"].items(), key=lambda x: x[1]["total"], reverse=True,
    ):
        if s["total"] < 10:
            continue
        rate = s["hit_l"] / s["total"]
        weight = max(0.15, min(0.40, rate * 0.6))
        print(f"    {key:<25} 적중률 {rate*100:5.1f}% ({s['total']:>4}건) → 가중치 {weight:.2f}")


def calc_candles_needed(unit: int, weeks: int, window: int) -> int:
    if unit == 1440:
        return weeks * 7 + window
    minutes_per_candle = unit
    return weeks * 7 * 24 * 60 // minutes_per_candle + window


def run():
    settings = Settings()
    client = BithumbCandle(settings)

    print(f"{'='*70}")
    print(f"  지표별 적중률 백테스트 — {len(MARKETS)}코인 × {len(TF_CONFIGS)}타임프레임 × {WEEKS}주")
    print(f"  코인: {', '.join(m.replace('KRW-','') for m in MARKETS)}")
    print(f"  타임프레임: {', '.join(TF_CONFIGS.keys())}")
    print(f"{'='*70}")

    # 타임프레임별로 실행
    for tf_name, cfg in TF_CONFIGS.items():
        unit = cfg["unit"]
        total_candles = calc_candles_needed(unit, WEEKS, cfg["window"])
        label_s = cfg["eval_label_short"]
        label_l = cfg["eval_label_long"]

        ref_unit = cfg["ref_unit"]
        ref_total = calc_candles_needed(ref_unit, WEEKS, 60) if ref_unit else 0

        print(f"\n{'='*70}")
        print(f"  ⏱️  타임프레임: {tf_name}")
        print(f"  평가: {label_s} / {label_l} 후")
        print(f"  캔들 수: 코인당 ~{total_candles}개")
        print(f"{'='*70}")

        tf_all_results: list[dict] = []

        for market in MARKETS:
            coin = market.replace("KRW-", "")
            print(f"\n  📊 {coin} ({tf_name})")

            print(f"    수집 중... ({total_candles}개)")
            t0 = time.time()
            candles = fetch_candles(client, market, unit, total_candles)
            df = candles_to_df(candles)
            elapsed = time.time() - t0
            print(
                f"    수집: {len(df)}개"
                f" ({df.iloc[0]['open_at'].strftime('%Y-%m-%d')}"
                f" ~ {df.iloc[-1]['open_at'].strftime('%Y-%m-%d')})"
                f" [{elapsed:.0f}s]"
            )

            ref_df = None
            if ref_unit:
                print(f"    상위TF({cfg['ref_label']}) 수집 중...")
                ref_candles = fetch_candles(client, market, ref_unit, ref_total)
                ref_df = candles_to_df(ref_candles)
                print(f"    수집: {len(ref_df)}개")

            results = []
            for idx in range(cfg["window"] - 1, len(df) - cfg["eval_long"], cfg["step"]):
                r = analyze_at(df, idx, cfg, ref_df)
                if r:
                    r["market"] = market
                    results.append(r)

            print(f"    S/R 트리거: {len(results)}건")

            if results:
                stats = compute_hit_rates(results)
                print_stats_table(
                    f"S/R 트리거 ({coin})", stats["sr"],
                    label_s, label_l, min_samples=3, show_avg=True,
                )
                print_stats_table(
                    f"확인 지표 ({coin})", stats["indicator"],
                    label_s, label_l, min_samples=3, show_avg=True,
                )

            tf_all_results.extend(results)

        # 타임프레임 통합 결과
        if tf_all_results:
            print(f"\n  {'='*70}")
            print(f"  📊 {tf_name} 통합 ({len(MARKETS)}코인, {len(tf_all_results)}건)")
            print(f"  {'='*70}")

            combined = compute_hit_rates(tf_all_results)
            print_stats_table(
                "📈 S/R 트리거별 적중률", combined["sr"],
                label_s, label_l, min_samples=10,
            )
            print_stats_table(
                "📊 확인 지표별 적중률", combined["indicator"],
                label_s, label_l, min_samples=10,
            )
            print_stats_table(
                "🔗 S/R + 지표 조합 (10건+)", combined["combo"],
                label_s, label_l, min_samples=10,
            )
            print_stats_table(
                "🚦 SMA 게이트 효과", combined["sma_gate"],
                label_s, label_l, min_samples=10, show_avg=False,
            )
            print_stats_table(
                "📉 상위 추세 효과", combined["trend"],
                label_s, label_l, min_samples=10, show_avg=False,
            )
            suggest_weights(combined, label_l)

    client.close()
    print(f"\n{'='*70}")
    print("전체 백테스트 완료")


if __name__ == "__main__":
    run()
