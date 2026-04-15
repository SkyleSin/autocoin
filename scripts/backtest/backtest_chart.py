"""백테스트 패턴 시각화 — 쌍봉/쌍바닥/하락쐐기 탐지 시점의 캔들차트 생성."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from bithumb.candle import BithumbCandle  # noqa: E402
from core.analysis.indicators import compute_all  # noqa: E402
from core.analysis.patterns import analyze as analyze_patterns  # noqa: E402
from core.analysis.signal_generator import evaluate  # noqa: E402
from core.analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from utils.config import Settings  # noqa: E402

MARKETS = ["KRW-BTC", "KRW-XRP"]
UNIT = 15
TIMEFRAME = "15m"
WINDOW = 200
STEP = 4
EVAL_BARS = 16
WEEKS = 4
TARGET_PATTERNS = {"double_top", "double_bottom", "falling_wedge"}
TARGET_NAMES = {"double_top": "쌍봉", "double_bottom": "쌍바닥", "falling_wedge": "하락쐐기형"}
OUT_DIR = Path(__file__).resolve().parent / "charts"


def fetch_candles(client, market, unit, total):
    all_candles = []
    to_param = None
    while len(all_candles) < total:
        batch_size = min(200, total - len(all_candles))
        batch = client.get_minutes(unit, market, count=batch_size, to=to_param)
        if not batch:
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


def candles_to_df(candles):
    df = pd.DataFrame(candles)
    df["open_at"] = pd.to_datetime(df["candle_date_time_utc"], utc=True)
    df = df.sort_values("open_at").reset_index(drop=True)
    df["open"] = df["opening_price"].astype(float)
    df["high"] = df["high_price"].astype(float)
    df["low"] = df["low_price"].astype(float)
    df["close"] = df["trade_price"].astype(float)
    df["volume"] = df["candle_acc_trade_volume"].astype(float)
    return df


def find_pattern_signals(df):
    """패턴 탐지 시점 찾기."""
    found = []
    for idx in range(WINDOW - 1, len(df) - EVAL_BARS, STEP):
        start = idx - WINDOW + 1
        window = df.iloc[start:idx + 1]
        highs = pd.Series(window["high"].values)
        lows = pd.Series(window["low"].values)
        closes = pd.Series(window["close"].values)

        snap = compute_all(closes)
        if snap is None:
            continue

        pat = analyze_patterns(highs, lows, closes, timeframe=TIMEFRAME)
        if not pat.patterns:
            continue

        for p in pat.patterns:
            if p.name in TARGET_PATTERNS:
                # 시그널 결과도 확인
                sr = analyze_sr(highs, lows, closes)
                signal = evaluate(
                    snap, float(closes.iloc[-1]),
                    pattern_analysis=pat, sr_analysis=sr,
                )

                future_price = float(df.iloc[idx + EVAL_BARS]["close"])
                entry_price = float(closes.iloc[-1])
                pct_change = (future_price - entry_price) / entry_price * 100

                if p.signal == "BUY":
                    hit = pct_change > 0
                elif p.signal == "SELL":
                    hit = pct_change < 0
                else:
                    hit = None

                found.append({
                    "idx": idx,
                    "start": start,
                    "pattern_name": p.name,
                    "pattern_ko": p.name_ko,
                    "pattern_signal": p.signal,
                    "pattern_confidence": p.confidence,
                    "pattern_desc": p.description,
                    "final_signal": signal.signal_type,
                    "entry_price": entry_price,
                    "future_price": future_price,
                    "pct_change": round(pct_change, 3),
                    "hit": hit,
                    "time": df.iloc[idx]["open_at"],
                    "resistance_line": pat.resistance_line,
                    "support_line": pat.support_line,
                })
                break  # 1패턴만
    return found


def draw_chart(df, info, market, chart_idx, out_dir):
    """개별 패턴 차트 생성. 최근 80봉 + 미래 16봉."""
    idx = info["idx"]
    show_start = max(0, idx - 79)
    show_end = min(len(df), idx + EVAL_BARS + 1)
    chart_df = df.iloc[show_start:show_end].copy()

    # mplfinance용 DataFrame 준비
    ohlc = chart_df[["open_at", "open", "high", "low", "close", "volume"]].copy()
    ohlc = ohlc.set_index("open_at")
    ohlc.index.name = "Date"
    ohlc.columns = ["Open", "High", "Low", "Close", "Volume"]

    # 시그널 시점 마커
    signal_idx_in_chart = idx - show_start
    future_idx_in_chart = idx + EVAL_BARS - show_start

    # 추가 플롯: 시그널 지점, 미래 가격
    marker_data = [float("nan")] * len(ohlc)
    future_data = [float("nan")] * len(ohlc)

    if 0 <= signal_idx_in_chart < len(ohlc):
        marker_data[signal_idx_in_chart] = info["entry_price"]
    if 0 <= future_idx_in_chart < len(ohlc):
        future_data[future_idx_in_chart] = info["future_price"]

    add_plots = []

    # 시그널 마커
    color = "blue" if info["pattern_signal"] == "BUY" else "red"
    add_plots.append(mpf.make_addplot(
        marker_data, type="scatter", markersize=200, marker="^" if color == "blue" else "v",
        color=color,
    ))
    # 미래 가격 마커
    hit_color = "green" if info["hit"] else "orange"
    add_plots.append(mpf.make_addplot(
        future_data, type="scatter", markersize=150, marker="D",
        color=hit_color,
    ))

    # 추세선 그리기 (hlines로 대체 — 패턴 설명에 포함된 가격선)
    hlines_dict = {}
    if info.get("resistance_line"):
        res = info["resistance_line"]
        res_price = res.price_at(WINDOW - 1)
        hlines_prices = [res_price]
        hlines_colors = ["red"]
        if info.get("support_line"):
            sup = info["support_line"]
            sup_price = sup.price_at(WINDOW - 1)
            hlines_prices.append(sup_price)
            hlines_colors.append("green")
        hlines_dict = dict(
            hlines=dict(
                hlines=hlines_prices,
                colors=hlines_colors,
                linestyle="--",
                linewidths=1,
            ),
        )

    # 차트 제목
    hit_str = "✅ 적중" if info["hit"] else "❌ 미적중"
    if info["hit"] is None:
        hit_str = "⬜ HOLD"
    title = (
        f"{market} — {info['pattern_ko']} ({info['pattern_signal']})\n"
        f"{info['time'].strftime('%Y-%m-%d %H:%M')} KST | "
        f"진입 {info['entry_price']:,.0f} → 4h후 {info['future_price']:,.0f} "
        f"({info['pct_change']:+.3f}%) {hit_str}"
    )

    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        rc={"font.size": 9},
    )

    fig, axes = mpf.plot(
        ohlc,
        type="candle",
        style=style,
        title=title,
        volume=True,
        addplot=add_plots if add_plots else None,
        figsize=(14, 7),
        returnfig=True,
        **hlines_dict,
    )

    # 시그널 시점에 세로선
    if 0 <= signal_idx_in_chart < len(ohlc):
        for ax in axes[:2]:
            ax.axvline(x=signal_idx_in_chart, color="purple", linestyle=":", alpha=0.5)

    # 미래 시점에 세로선
    if 0 <= future_idx_in_chart < len(ohlc):
        for ax in axes[:2]:
            ax.axvline(x=future_idx_in_chart, color="gray", linestyle=":", alpha=0.5)

    # 패턴 설명 텍스트
    axes[0].text(
        0.01, 0.97, info["pattern_desc"],
        transform=axes[0].transAxes, fontsize=8,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    fname = f"{chart_idx:02d}_{market}_{info['pattern_name']}_{info['time'].strftime('%m%d_%H%M')}.png"
    fpath = out_dir / fname
    fig.savefig(fpath, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return fpath


def main():
    settings = Settings()
    client = BithumbCandle(settings)
    total_15m = WEEKS * 7 * 24 * 4 + WINDOW

    OUT_DIR.mkdir(exist_ok=True)

    # 한글 폰트 설정
    import matplotlib.font_manager as fm
    font_paths = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for fp in font_paths:
        if Path(fp).exists():
            prop = fm.FontProperties(fname=fp)
            plt.rcParams["font.family"] = prop.get_name()
            break
    else:
        plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["axes.unicode_minus"] = False

    chart_idx = 0
    for market in MARKETS:
        print(f"\n{'='*60}")
        print(f"📊 {market} — 패턴 탐색 중...")
        print(f"{'='*60}")

        candles = fetch_candles(client, market, UNIT, total_15m)
        df = candles_to_df(candles)
        print(f"  캔들 {len(df)}개 로드")

        signals = find_pattern_signals(df)
        print(f"  대상 패턴 탐지: {len(signals)}건")

        # 패턴별 분류
        by_pattern: dict[str, list] = {}
        for s in signals:
            by_pattern.setdefault(s["pattern_name"], []).append(s)

        for pat_name, pat_signals in by_pattern.items():
            ko_name = TARGET_NAMES.get(pat_name, pat_name)
            hits = sum(1 for s in pat_signals if s["hit"])
            total = len(pat_signals)
            rate = hits / total * 100 if total else 0
            print(f"\n  [{ko_name}] {total}건 (적중 {hits}/{total} = {rate:.0f}%)")

            # 최대 5개 차트 생성 (적중/미적중 섞어서)
            # 적중 2개 + 미적중 2개 + 나머지 1개 (시간순)
            hit_list = [s for s in pat_signals if s["hit"]]
            miss_list = [s for s in pat_signals if not s["hit"]]
            selected = []
            selected.extend(hit_list[:2])
            selected.extend(miss_list[:2])
            remaining = [s for s in pat_signals if s not in selected]
            selected.extend(remaining[:1])
            selected.sort(key=lambda s: s["time"])

            for info in selected:
                chart_idx += 1
                fpath = draw_chart(df, info, market, chart_idx, OUT_DIR)
                hit_str = "✅" if info["hit"] else "❌"
                print(
                    f"    {hit_str} {info['time'].strftime('%m/%d %H:%M')}"
                    f" {info['pct_change']:+.3f}% → {fpath.name}"
                )

    client.close()
    print(f"\n{'='*60}")
    print(f"차트 {chart_idx}개 생성 완료 → {OUT_DIR}/")


if __name__ == "__main__":
    main()
