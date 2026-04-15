"""현재 전략 시뮬레이션 — 30일치 15분봉 기반.

현재 전략: BUY 시그널 → 포지션 사이징 매수, SELL 시그널 → 전량 매도
LLM 호출 횟수, 예상 수익, 거래 이력을 출력합니다.

사용법: cd /home/skyle/autocoin && .venv/bin/python scripts/backtest/simulate_current.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from bithumb.candle import BithumbCandle  # noqa: E402
from core.analysis.indicators import compute_all, sma  # noqa: E402
from core.analysis.patterns import analyze as analyze_patterns  # noqa: E402
from core.analysis.signal_generator import evaluate  # noqa: E402
from core.analysis.support_resistance import analyze as analyze_sr  # noqa: E402
from core.strategy.executor.position_sizing import calculate_position  # noqa: E402
from utils.config import Settings  # noqa: E402

# ── 설정 ──
MARKETS = ["KRW-BTC", "KRW-XRP", "KRW-SOL", "KRW-TRX"]
UNIT = 15
TIMEFRAME = "15m"
WINDOW = 200
STEP = 1  # 매 캔들마다 평가
DAYS = 30
REF_TF_UNIT = 60
REF_TF_LABEL = "1h"

INITIAL_KRW = 1_000_000
FEE_RATE = 0.0025  # 빗썸 0.25%
COOLDOWN_BARS = 16  # 4시간 = 16 * 15분
MAX_PER_TRADE_PCT = 15.0

# DeepSeek R1 비용 추정 (호출당)
LLM_COST_PER_CALL_USD = 0.003


@dataclass
class Position:
    market: str
    qty: float
    avg_price: float
    entry_time: str


@dataclass
class Trade:
    market: str
    side: str  # BUY / SELL
    price: float
    qty: float
    amount_krw: float
    fee: float
    time: str
    confidence: float
    reasons: list[str]
    pnl: float = 0.0  # SELL 시 실현 손익


@dataclass
class SimState:
    krw: float = INITIAL_KRW
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    last_signal_bar: dict[str, int] = field(default_factory=dict)  # 쿨다운
    llm_calls: int = 0
    signals_total: int = 0
    signals_buy: int = 0
    signals_sell: int = 0
    signals_hold: int = 0
    filtered_mtf: int = 0
    cooldown_skipped: int = 0


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

    total_15m = DAYS * 24 * 4 + WINDOW + 50  # 여유분
    total_1h = DAYS * 24 + 60

    print(f"{'='*70}")
    print(f"  현재 전략 시뮬레이션 — {DAYS}일 | 초기 자본 {INITIAL_KRW:,.0f} KRW")
    print("  전략: BUY → 포지션 사이징 매수 | SELL → 전량 매도")
    print(f"  수수료: {FEE_RATE*100:.2f}% | 쿨다운: 4시간 | 마켓: {len(MARKETS)}개")
    print(f"{'='*70}\n")

    all_market_data: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    # ── 데이터 수집 ──
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

    # ── 시뮬레이션 실행 ──
    print(f"\n{'='*70}")
    print("  시뮬레이션 실행 중...")
    print(f"{'='*70}\n")

    for market, (df, ref_df) in all_market_data.items():
        bar_offset = 0

        for idx in range(WINDOW - 1, len(df), STEP):
            bar_offset += 1
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

            # MTF 필터
            current_time = df.iloc[idx]["open_at"]
            higher_trend = get_higher_trend(ref_df, current_time)

            if signal.signal_type == "BUY" and higher_trend == "DOWN":
                state.filtered_mtf += 1
                continue

            # SELL + 상위 상승추세 → 신뢰도 50% 감쇠
            confidence = signal.confidence
            if signal.signal_type == "SELL" and higher_trend == "UP":
                confidence *= 0.5

            # 쿨다운 체크 (4시간)
            last_bar = state.last_signal_bar.get(market, -999)
            global_bar = idx  # 절대 인덱스
            if global_bar - last_bar < COOLDOWN_BARS:
                state.cooldown_skipped += 1
                continue

            # LLM 호출 카운트 (실제론 여기서 LLM 호출)
            state.llm_calls += 1

            if signal.signal_type == "BUY":
                state.signals_buy += 1
            else:
                state.signals_sell += 1

            time_str = str(df.iloc[idx]["open_at"])[:19]

            # ── 매수 ──
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
                state.last_signal_bar[market] = global_bar

            # ── 매도 (전량) ──
            elif signal.signal_type == "SELL" and market in state.positions:
                pos = state.positions[market]
                sell_amount = pos.qty * close_price
                fee = sell_amount * FEE_RATE
                net = sell_amount - fee
                pnl = net - (pos.qty * pos.avg_price)

                state.krw += net
                del state.positions[market]
                state.trades.append(Trade(
                    market=market, side="SELL", price=close_price, qty=pos.qty,
                    amount_krw=sell_amount, fee=fee, time=time_str,
                    confidence=confidence, reasons=signal.reasons, pnl=pnl,
                ))
                state.last_signal_bar[market] = global_bar

    # ── 미청산 포지션 평가 ──
    unrealized = 0.0
    for market, pos in state.positions.items():
        df, _ = all_market_data[market]
        last_price = float(df.iloc[-1]["close"])
        unrealized += pos.qty * last_price - pos.qty * pos.avg_price

    # ── 결과 출력 ──
    total_invested = sum(
        t.amount_krw for t in state.trades if t.side == "BUY"
    )
    total_fees = sum(t.fee for t in state.trades)
    realized_pnl = sum(t.pnl for t in state.trades if t.side == "SELL")
    positions_value = sum(
        pos.qty * float(all_market_data[m][0].iloc[-1]["close"])
        for m, pos in state.positions.items()
    )
    total_value = state.krw + positions_value
    total_return = (total_value - INITIAL_KRW) / INITIAL_KRW * 100

    buy_trades = [t for t in state.trades if t.side == "BUY"]
    sell_trades = [t for t in state.trades if t.side == "SELL"]
    winning = [t for t in sell_trades if t.pnl > 0]

    print(f"\n{'='*70}")
    print("  시뮬레이션 결과 — 현재 전략 (전량 매도)")
    print(f"{'='*70}")

    print("\n  [수익]")
    print(f"  초기 자본:        {INITIAL_KRW:>14,.0f} KRW")
    print(f"  최종 평가:        {total_value:>14,.0f} KRW")
    print(f"  실현 손익:        {realized_pnl:>+14,.0f} KRW")
    print(f"  미실현 손익:      {unrealized:>+14,.0f} KRW")
    print(f"  총 수수료:        {total_fees:>14,.0f} KRW")
    print(f"  총 수익률:        {total_return:>+13.2f}%")

    print("\n  [거래]")
    print(f"  매수:             {len(buy_trades):>10}건")
    print(f"  매도:             {len(sell_trades):>10}건")
    if sell_trades:
        print(f"  승률:             {len(winning)/len(sell_trades)*100:>9.1f}%")
        avg_win = (
            sum(t.pnl for t in winning) / len(winning) if winning else 0
        )
        losing = [t for t in sell_trades if t.pnl <= 0]
        avg_loss = sum(t.pnl for t in losing) / len(losing) if losing else 0
        print(f"  평균 수익 (승):   {avg_win:>+14,.0f} KRW")
        print(f"  평균 손실 (패):   {avg_loss:>+14,.0f} KRW")

    print("\n  [시그널]")
    print(f"  기술분석 총:      {state.signals_total:>10}건")
    print(f"  HOLD:             {state.signals_hold:>10}건")
    print(f"  MTF 필터:         {state.filtered_mtf:>10}건")
    print(f"  쿨다운 스킵:      {state.cooldown_skipped:>10}건")
    print(f"  LLM 호출:         {state.llm_calls:>10}건 (BUY {state.signals_buy}"
          f" / SELL {state.signals_sell})")

    print("\n  [LLM 비용 추정]")
    print(f"  호출 수:          {state.llm_calls:>10}건")
    print(f"  단가:             ${LLM_COST_PER_CALL_USD:.3f}/건")
    print(f"  30일 비용:        ${state.llm_calls * LLM_COST_PER_CALL_USD:>9.2f}")

    # ── 미청산 포지션 ──
    if state.positions:
        print("\n  [미청산 포지션]")
        for m, pos in state.positions.items():
            last_price = float(all_market_data[m][0].iloc[-1]["close"])
            pos_pnl = (last_price - pos.avg_price) / pos.avg_price * 100
            print(f"  {m}: 진입 {pos.avg_price:,.0f} → 현재 {last_price:,.0f}"
                  f" ({pos_pnl:+.2f}%)")

    # ── 거래 이력 ──
    if state.trades:
        print("\n  [거래 이력]")
        print(f"  {'시각':<20} {'마켓':<10} {'방향':>4} {'가격':>14}"
              f" {'금액':>12} {'손익':>12}")
        print(f"  {'─'*75}")
        for t in state.trades:
            pnl_str = f"{t.pnl:>+12,.0f}" if t.side == "SELL" else f"{'':>12}"
            print(f"  {t.time:<20} {t.market:<10} {t.side:>4}"
                  f" {t.price:>14,.0f} {t.amount_krw:>12,.0f} {pnl_str}")

    print(f"\n{'='*70}")
    print("  시뮬레이션 완료")
    print(f"{'='*70}")

    return state


if __name__ == "__main__":
    run_simulation()
