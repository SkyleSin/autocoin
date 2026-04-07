"""알림 빈도 시뮬레이션 — 실제 운영과 동일한 조건으로 alert_service 로직 적용."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from analysis.indicators import compute_all, sma  # noqa: E402
from analysis.patterns import analyze as analyze_patterns  # noqa: E402
from analysis.signal_generator import evaluate  # noqa: E402
from analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from bithumb.candle import BithumbCandle  # noqa: E402
from utils.config import Settings  # noqa: E402

MARKETS = ["KRW-BTC", "KRW-XRP"]
UNIT = 15
TIMEFRAME = "15m"
WINDOW = 200
WEEKS = 4
REMIND_INTERVAL = 14400  # 4시간 (초)
CANDLE_INTERVAL = 900  # 15분 (초)


def fetch_candles(client, market, unit, total):
    all_candles = []
    to_param = None
    while len(all_candles) < total:
        batch = client.get_minutes(unit, market, count=min(200, total - len(all_candles)), to=to_param)
        if not batch:
            break
        all_candles.extend(batch)
        to_param = batch[-1]["candle_date_time_utc"]
        time.sleep(0.15)
    all_candles.sort(key=lambda c: c["candle_date_time_utc"])
    seen = set()
    return [c for c in all_candles if (k := (c["market"], c["candle_date_time_utc"])) not in seen and not seen.add(k)]


def candles_to_df(candles):
    df = pd.DataFrame(candles)
    df["open_at"] = pd.to_datetime(df["candle_date_time_utc"], utc=True)
    df = df.sort_values("open_at").reset_index(drop=True)
    for col, src in [("open", "opening_price"), ("high", "high_price"),
                     ("low", "low_price"), ("close", "trade_price"),
                     ("volume", "candle_acc_trade_volume")]:
        df[col] = df[src].astype(float)
    return df


def simulate_alerts(df, ref_df):
    """운영 환경 시뮬레이션: 15분마다 분석, alert_service 로직 적용."""
    # alert_service 상태
    active_alert = None  # {"signal_type", "last_alerted_at", "passed_at"}
    alerts_sent = []
    signals_generated = []

    for idx in range(WINDOW - 1, len(df)):
        start = idx - WINDOW + 1
        window = df.iloc[start:idx + 1]
        highs = pd.Series(window["high"].values)
        lows = pd.Series(window["low"].values)
        closes = pd.Series(window["close"].values)
        volumes = pd.Series(window["volume"].values)

        snap = compute_all(closes, volumes)
        if snap is None:
            continue

        close_price = float(closes.iloc[-1])
        pat = analyze_patterns(highs, lows, closes, timeframe=TIMEFRAME)
        sr = analyze_sr(highs, lows, closes)
        signal = evaluate(snap, close_price, pattern_analysis=pat, sr_analysis=sr)

        # MTF 필터
        if signal.signal_type == "BUY" and ref_df is not None:
            current_time = df.iloc[idx]["open_at"]
            ref_before = ref_df[ref_df["open_at"] <= current_time]
            if len(ref_before) >= 50:
                ref_closes = pd.Series(ref_before["close"].values[-60:])
                sma_20 = sma(ref_closes, 20)
                sma_50 = sma(ref_closes, 50)
                last_20, last_50 = float(sma_20.iloc[-1]), float(sma_50.iloc[-1])
                recent = sma_20.iloc[-3:]
                slope = (float(recent.iloc[-1]) - float(recent.iloc[0])) / float(recent.iloc[0]) * 100
                if last_20 < last_50 and slope < 0:
                    signal = type(signal)("HOLD", 0.0, signal.reasons + ["MTF 하락→매수 차단"])

        current_time_ts = df.iloc[idx]["open_at"].timestamp()
        signals_generated.append(signal.signal_type)

        # LLM 필터 시뮬레이션: BUY/SELL만 LLM에 전달, ~30% 정도 HOLD로 전환 가정
        # 실제로는 LLM이 판단하지만, 여기서는 시그널만 카운트

        if signal.signal_type == "HOLD":
            continue  # HOLD → 기존 알림 유지, 스킵

        # alert_service 로직
        if active_alert is None:
            # 신규 알림
            active_alert = {"signal_type": signal.signal_type, "last_alerted_at": current_time_ts}
            alerts_sent.append({
                "time": str(df.iloc[idx]["open_at"]),
                "type": "NEW",
                "signal": signal.signal_type,
                "reasons": signal.reasons,
            })
        elif active_alert["signal_type"] != signal.signal_type:
            # 시그널 변경 — 쿨다운 확인
            elapsed = current_time_ts - active_alert["last_alerted_at"]
            if elapsed >= REMIND_INTERVAL:
                active_alert = {"signal_type": signal.signal_type, "last_alerted_at": current_time_ts}
                alerts_sent.append({
                    "time": str(df.iloc[idx]["open_at"]),
                    "type": "CHANGED",
                    "signal": signal.signal_type,
                    "reasons": signal.reasons,
                })
            else:
                # 쿨다운 중 → 상태만 업데이트
                active_alert["signal_type"] = signal.signal_type
        else:
            # 동일 시그널 — 재알림 확인
            elapsed = current_time_ts - active_alert["last_alerted_at"]
            if elapsed >= REMIND_INTERVAL:
                active_alert["last_alerted_at"] = current_time_ts
                alerts_sent.append({
                    "time": str(df.iloc[idx]["open_at"]),
                    "type": "REMIND",
                    "signal": signal.signal_type,
                    "reasons": signal.reasons,
                })

    return alerts_sent, signals_generated


def main():
    settings = Settings()
    client = BithumbCandle(settings)
    total_15m = WEEKS * 7 * 24 * 4 + WINDOW
    total_1h = WEEKS * 7 * 24 + 60

    print(f"=== 알림 빈도 시뮬레이션 ({WEEKS}주) ===")
    print(f"수집 주기: {CANDLE_INTERVAL}초, 쿨다운: {REMIND_INTERVAL}초\n")

    for market in MARKETS:
        print(f"\n{'='*60}")
        print(f"📊 {market}")
        print(f"{'='*60}")

        candles = fetch_candles(client, market, UNIT, total_15m)
        df = candles_to_df(candles)

        candles_1h = fetch_candles(client, market, 60, total_1h)
        ref_df = candles_to_df(candles_1h)

        alerts, signals = simulate_alerts(df, ref_df)

        days = WEEKS * 7
        total_signals = len([s for s in signals if s != "HOLD"])
        buy_signals = len([s for s in signals if s == "BUY"])
        sell_signals = len([s for s in signals if s == "SELL"])

        new_alerts = [a for a in alerts if a["type"] == "NEW"]
        changed_alerts = [a for a in alerts if a["type"] == "CHANGED"]
        remind_alerts = [a for a in alerts if a["type"] == "REMIND"]

        print(f"\n  시그널 발생 (LLM 전달 전): {total_signals}회"
              f" (BUY {buy_signals} / SELL {sell_signals})")
        print(f"  하루 평균 시그널: {total_signals / days:.1f}회")

        print(f"\n  📬 실제 알림 (alert_service 적용 후):")
        print(f"  총 알림: {len(alerts)}회")
        print(f"    NEW: {len(new_alerts)}회")
        print(f"    CHANGED: {len(changed_alerts)}회")
        print(f"    REMIND: {len(remind_alerts)}회")
        print(f"  하루 평균 알림: {len(alerts) / days:.1f}회")

        # 날짜별 알림 분포
        from collections import Counter
        daily = Counter()
        for a in alerts:
            day = a["time"][:10]
            daily[day] += 1

        if daily:
            max_day = max(daily.values())
            min_day = min(daily.values())
            no_alert_days = days - len(daily)
            print(f"\n  📅 일별 분포:")
            print(f"    알림 없는 날: {no_alert_days}일")
            print(f"    최소: {min_day}회/일, 최대: {max_day}회/일")

        # 최근 알림 20개
        print(f"\n  📋 최근 알림 (최대 20개)")
        print(f"  {'시각':<22} {'유형':>7} {'시그널':>4}  근거")
        print(f"  {'─'*75}")
        for a in alerts[-20:]:
            reasons_short = ", ".join(r.split("(")[0].strip() for r in a["reasons"][:2])
            print(f"  {a['time'][:19]:<22} {a['type']:>7} {a['signal']:>4}  {reasons_short}")

    client.close()
    print(f"\n{'='*60}")
    print("시뮬레이션 완료")


if __name__ == "__main__":
    main()
