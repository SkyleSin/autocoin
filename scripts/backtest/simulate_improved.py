"""개선 전략 시뮬레이션 — 확신도 기반 부분 매도 + 재진입.

현재 전략과 동일한 데이터·설정으로 비교합니다.
개선점:
  1. SELL → 확신도 기반 부분 매도 (20~80%)
  2. 재진입: 같은 방향 SELL 시 잔여 포지션 추가 매도
  3. BUY: 포지션 없을 때만 (현재와 동일)

사용법: cd /home/skyle/autocoin && .venv/bin/python scripts/backtest/simulate_improved.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from analysis.indicators import compute_all, sma  # noqa: E402
from analysis.patterns import analyze as analyze_patterns  # noqa: E402
from analysis.signal_generator import evaluate  # noqa: E402
from analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from bithumb.candle import BithumbCandle  # noqa: E402
from executor.position_sizing import calculate_position  # noqa: E402
from utils.config import Settings  # noqa: E402

# ── 설정 (현재 전략과 동일) ──
MARKETS = ["KRW-BTC", "KRW-XRP", "KRW-SOL", "KRW-TRX"]
UNIT = 15
TIMEFRAME = "15m"
WINDOW = 200
STEP = 1
DAYS = 30
REF_TF_UNIT = 60
REF_TF_LABEL = "1h"

INITIAL_KRW = 1_000_000
FEE_RATE = 0.0025
COOLDOWN_BARS = 16  # 4시간
MAX_PER_TRADE_PCT = 15.0
LLM_COST_PER_CALL_USD = 0.003

# ── 개선 전략 파라미터 ──
# 확신도 → 매도 비율 매핑
SELL_RATIO_BY_CONFIDENCE = [
    (0.8, 0.80),  # 확신도 80%+ → 80% 매도
    (0.6, 0.60),  # 확신도 60%+ → 60% 매도
    (0.4, 0.40),  # 확신도 40%+ → 40% 매도
    (0.0, 0.20),  # 그 외 → 20% 매도
]

# 잔여 포지션 최소 가치 (이하면 전량 청산)
MIN_POSITION_KRW = 5_000


@dataclass
class Position:
    market: str
    qty: float
    avg_price: float
    entry_time: str
    sell_count: int = 0  # 부분 매도 횟수


@dataclass
class Trade:
    market: str
    side: str
    price: float
    qty: float
    amount_krw: float
    fee: float
    time: str
    confidence: float
    reasons: list[str]
    pnl: float = 0.0
    sell_ratio: float = 0.0  # 매도 비율 (SELL만)
    is_reentry: bool = False  # 재진입 여부


@dataclass
class SimState:
    krw: float = INITIAL_KRW
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    last_signal_bar: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0
    signals_total: int = 0
    signals_buy: int = 0
    signals_sell: int = 0
    signals_hold: int = 0
    filtered_mtf: int = 0
    cooldown_skipped: int = 0
    reentry_sells: int = 0  # 재진입 매도 횟수


def get_sell_ratio(confidence: float) -> float:
    """확신도에 따른 매도 비율 결정."""
    for threshold, ratio in SELL_RATIO_BY_CONFIDENCE:
        if confidence >= threshold:
            return ratio
    return 0.20


def fetch_candles(
    client: BithumbCandle, market: str, unit: int, total: int,
) -> list[dict]:
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


def run_simulation():
    settings = Settings()
    client = BithumbCandle(settings)
    state = SimState()

    total_15m = DAYS * 24 * 4 + WINDOW + 50
    total_1h = DAYS * 24 + 60

    print(f"{'='*70}")
    print(f"  개선 전략 시뮬레이션 — {DAYS}일 | 초기 자본 {INITIAL_KRW:,.0f} KRW")
    print(f"  전략: BUY → 포지션 사이징 매수 | SELL → 확신도 기반 부분 매도 + 재진입")
    print(f"  매도 비율: conf>=80%→80%, >=60%→60%, >=40%→40%, 그외→20%")
    print(f"  수수료: {FEE_RATE*100:.2f}% | 쿨다운: 4시간 | 마켓: {len(MARKETS)}개")
    print(f"{'='*70}\n")

    all_market_data: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    for market in MARKETS:
        print(f"  [{market}] 15분봉 수집 중... (약 {total_15m}개)")
        candles_15m = fetch_candles(client, market, UNIT, total_15m)
        df = candles_to_df(candles_15m)
        print(f"    수집: {len(df)}개 ({str(df.iloc[0]['open_at'])[:10]} ~ "
              f"{str(df.iloc[-1]['open_at'])[:10]})")

        print(f"  [{market}] 1시간봉 수집 중...")
        candles_1h = fetch_candles(client, market, REF_TF_UNIT, total_1h)
        ref_df = candles_to_df(candles_1h)
        print(f"    수집: {len(ref_df)}개\n")

        all_market_data[market] = (df, ref_df)

    client.close()

    print(f"\n{'='*70}")
    print(f"  시뮬레이션 실행 중...")
    print(f"{'='*70}\n")

    for market, (df, ref_df) in all_market_data.items():
        for idx in range(WINDOW - 1, len(df), STEP):
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

            state.signals_total += 1

            if signal.signal_type == "HOLD":
                state.signals_hold += 1
                continue

            current_time = df.iloc[idx]["open_at"]
            higher_trend = get_higher_trend(ref_df, current_time)

            if signal.signal_type == "BUY" and higher_trend == "DOWN":
                state.filtered_mtf += 1
                continue

            confidence = signal.confidence
            if signal.signal_type == "SELL" and higher_trend == "UP":
                confidence *= 0.5

            # 쿨다운 체크
            last_bar = state.last_signal_bar.get(market, -999)
            if idx - last_bar < COOLDOWN_BARS:
                state.cooldown_skipped += 1
                continue

            state.llm_calls += 1
            time_str = str(df.iloc[idx]["open_at"])[:19]

            if signal.signal_type == "BUY":
                state.signals_buy += 1
            else:
                state.signals_sell += 1

            # ── 매수: 포지션 없을 때만 ──
            if signal.signal_type == "BUY" and market not in state.positions:
                patterns = []
                if pat and pat.patterns:
                    patterns = [
                        {"name": p.name, "name_ko": p.name_ko, "confidence": p.confidence}
                        for p in pat.patterns[:1]
                    ]
                indicators = {
                    "rsi_14": snap.rsi_14,
                    "bb_upper": snap.bb_upper,
                    "bb_lower": snap.bb_lower,
                    "bb_middle": snap.bb_middle,
                }
                ps = calculate_position(
                    state.krw,
                    patterns=patterns,
                    confidence=confidence,
                    indicators=indicators,
                    max_per_trade_pct=MAX_PER_TRADE_PCT,
                )
                amount = ps.amount_krw
                if amount < 5000 or amount > state.krw:
                    continue

                fee = amount * FEE_RATE
                qty = (amount - fee) / close_price
                state.krw -= amount
                state.positions[market] = Position(
                    market=market, qty=qty, avg_price=close_price, entry_time=time_str,
                )
                state.trades.append(Trade(
                    market=market, side="BUY", price=close_price, qty=qty,
                    amount_krw=amount, fee=fee, time=time_str,
                    confidence=confidence, reasons=signal.reasons,
                ))
                state.last_signal_bar[market] = idx

            # ── 매도: 확신도 기반 부분 매도 + 재진입 ──
            elif signal.signal_type == "SELL" and market in state.positions:
                pos = state.positions[market]
                is_reentry = pos.sell_count > 0

                sell_ratio = get_sell_ratio(confidence)

                # 잔여 가치가 작으면 전량 청산
                remaining_value = pos.qty * close_price
                if remaining_value * (1 - sell_ratio) < MIN_POSITION_KRW:
                    sell_ratio = 1.0

                sell_qty = pos.qty * sell_ratio
                sell_amount = sell_qty * close_price
                fee = sell_amount * FEE_RATE
                net = sell_amount - fee
                pnl = net - (sell_qty * pos.avg_price)

                state.krw += net

                if is_reentry:
                    state.reentry_sells += 1

                state.trades.append(Trade(
                    market=market, side="SELL", price=close_price, qty=sell_qty,
                    amount_krw=sell_amount, fee=fee, time=time_str,
                    confidence=confidence, reasons=signal.reasons,
                    pnl=pnl, sell_ratio=sell_ratio, is_reentry=is_reentry,
                ))

                # 포지션 업데이트
                pos.qty -= sell_qty
                pos.sell_count += 1

                if pos.qty * close_price < MIN_POSITION_KRW or sell_ratio >= 1.0:
                    # 잔여분 정리
                    if pos.qty > 0:
                        leftover = pos.qty * close_price
                        leftover_fee = leftover * FEE_RATE
                        state.krw += leftover - leftover_fee
                    del state.positions[market]
                else:
                    state.positions[market] = pos

                state.last_signal_bar[market] = idx

    # ── 미청산 포지션 평가 ──
    unrealized = 0.0
    positions_value = 0.0
    for market, pos in state.positions.items():
        df, _ = all_market_data[market]
        last_price = float(df.iloc[-1]["close"])
        positions_value += pos.qty * last_price
        unrealized += pos.qty * last_price - pos.qty * pos.avg_price

    total_value = state.krw + positions_value
    total_return = (total_value - INITIAL_KRW) / INITIAL_KRW * 100

    total_fees = sum(t.fee for t in state.trades)
    realized_pnl = sum(t.pnl for t in state.trades if t.side == "SELL")

    buy_trades = [t for t in state.trades if t.side == "BUY"]
    sell_trades = [t for t in state.trades if t.side == "SELL"]
    winning = [t for t in sell_trades if t.pnl > 0]
    losing = [t for t in sell_trades if t.pnl <= 0]

    # 완전 청산된 포지션별 수익 추적
    # (매수 1회 → 매도 N회의 합산 손익)
    position_pnls: list[float] = []
    current_pnl: dict[str, float] = {}
    for t in state.trades:
        if t.side == "BUY":
            current_pnl[t.market] = 0.0
        elif t.side == "SELL":
            if t.market in current_pnl:
                current_pnl[t.market] += t.pnl
                if t.sell_ratio >= 1.0 or t.market not in state.positions:
                    position_pnls.append(current_pnl.pop(t.market, 0.0))

    position_wins = [p for p in position_pnls if p > 0]
    position_losses = [p for p in position_pnls if p <= 0]

    print(f"\n{'='*70}")
    print(f"  시뮬레이션 결과 — 개선 전략 (확신도 기반 부분 매도 + 재진입)")
    print(f"{'='*70}")

    print(f"\n  [수익]")
    print(f"  초기 자본:        {INITIAL_KRW:>14,.0f} KRW")
    print(f"  최종 평가:        {total_value:>14,.0f} KRW")
    print(f"  실현 손익:        {realized_pnl:>+14,.0f} KRW")
    print(f"  미실현 손익:      {unrealized:>+14,.0f} KRW")
    print(f"  총 수수료:        {total_fees:>14,.0f} KRW")
    print(f"  총 수익률:        {total_return:>+13.2f}%")

    print(f"\n  [거래]")
    print(f"  매수:             {len(buy_trades):>10}건")
    print(f"  매도:             {len(sell_trades):>10}건 (재진입 {state.reentry_sells}건)")
    if sell_trades:
        print(f"  매도 승률:        {len(winning)/len(sell_trades)*100:>9.1f}%")
    if position_pnls:
        print(f"  포지션 승률:      "
              f"{len(position_wins)/len(position_pnls)*100:>9.1f}% "
              f"({len(position_wins)}/{len(position_pnls)})")
        if position_wins:
            print(f"  평균 수익 (승):   {sum(position_wins)/len(position_wins):>+14,.0f} KRW")
        if position_losses:
            print(f"  평균 손실 (패):   "
                  f"{sum(position_losses)/len(position_losses):>+14,.0f} KRW")

    # 매도 비율 통계
    sell_ratios = [t.sell_ratio for t in sell_trades]
    if sell_ratios:
        print(f"\n  [매도 비율 분포]")
        for ratio in [0.2, 0.4, 0.6, 0.8, 1.0]:
            cnt = sum(1 for r in sell_ratios if abs(r - ratio) < 0.01)
            if cnt:
                print(f"  {ratio*100:>5.0f}% 매도:      {cnt:>10}건")

    print(f"\n  [시그널]")
    print(f"  기술분석 총:      {state.signals_total:>10}건")
    print(f"  HOLD:             {state.signals_hold:>10}건")
    print(f"  MTF 필터:         {state.filtered_mtf:>10}건")
    print(f"  쿨다운 스킵:      {state.cooldown_skipped:>10}건")
    print(f"  LLM 호출:         {state.llm_calls:>10}건 (BUY {state.signals_buy}"
          f" / SELL {state.signals_sell})")

    print(f"\n  [LLM 비용 추정]")
    print(f"  호출 수:          {state.llm_calls:>10}건")
    print(f"  단가:             ${LLM_COST_PER_CALL_USD:.3f}/건")
    print(f"  30일 비용:        ${state.llm_calls * LLM_COST_PER_CALL_USD:>9.2f}")

    if state.positions:
        print(f"\n  [미청산 포지션]")
        for m, pos in state.positions.items():
            last_price = float(all_market_data[m][0].iloc[-1]["close"])
            pos_pnl = (last_price - pos.avg_price) / pos.avg_price * 100
            remaining_pct = pos.qty * last_price / INITIAL_KRW * 100
            print(f"  {m}: 진입 {pos.avg_price:,.0f} → 현재 {last_price:,.0f}"
                  f" ({pos_pnl:+.2f}%) | 매도 {pos.sell_count}회 완료"
                  f" | 잔여 {remaining_pct:.1f}%")

    # 거래 이력
    if state.trades:
        print(f"\n  [거래 이력]")
        print(f"  {'시각':<20} {'마켓':<10} {'방향':>4} {'가격':>14}"
              f" {'금액':>12} {'비율':>5} {'손익':>12} {'재진입':>6}")
        print(f"  {'─'*90}")
        for t in state.trades:
            pnl_str = f"{t.pnl:>+12,.0f}" if t.side == "SELL" else f"{'':>12}"
            ratio_str = f"{t.sell_ratio*100:>4.0f}%" if t.side == "SELL" else f"{'':>5}"
            re_str = "RE" if t.is_reentry else ""
            print(f"  {t.time:<20} {t.market:<10} {t.side:>4}"
                  f" {t.price:>14,.0f} {t.amount_krw:>12,.0f} {ratio_str}"
                  f" {pnl_str} {re_str:>6}")

    print(f"\n{'='*70}")
    print(f"  시뮬레이션 완료")
    print(f"{'='*70}")

    return state


if __name__ == "__main__":
    run_simulation()
