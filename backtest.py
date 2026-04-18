"""탑다운 MTF 전략 백테스트 — 빗썸 API에서 1년치 캔들 수집 + 시뮬레이션.

사용법: python backtest.py
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

KST = timezone(timedelta(hours=9))
BITHUMB_URL = "https://api.bithumb.com"

# ── 빗썸 캔들 수집 ──────────────────────────────────────────────


def fetch_candles(
    market: str, tf_minutes: int, days: int = 365, max_count: int = 200,
) -> pd.DataFrame:
    """빗썸 API에서 과거 캔들 수집. tf_minutes: 15, 60, 240, 1440(일봉)."""
    if tf_minutes >= 1440:
        url = f"{BITHUMB_URL}/v1/candles/days"
    elif tf_minutes >= 60:
        url = f"{BITHUMB_URL}/v1/candles/minutes/{tf_minutes}"
    else:
        url = f"{BITHUMB_URL}/v1/candles/minutes/{tf_minutes}"

    total_candles = (days * 24 * 60) // tf_minutes
    all_rows: list[dict] = []
    to_param: str | None = None

    with httpx.Client(timeout=15) as client:
        while len(all_rows) < total_candles:
            params: dict = {"market": market, "count": max_count}
            if to_param:
                params["to"] = to_param
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            all_rows.extend(data)
            oldest = data[-1]["candle_date_time_kst"]
            to_param = oldest
            time.sleep(0.12)  # rate limit

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["dt"] = pd.to_datetime(df["candle_date_time_kst"])
    df = df.sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
    df = df.rename(columns={
        "opening_price": "open",
        "high_price": "high",
        "low_price": "low",
        "trade_price": "close",
        "candle_acc_trade_volume": "volume",
    })
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df[["dt", "open", "high", "low", "close", "volume"]]


# ── 지표 계산 ────────────────────────────────────────────────────


@dataclass
class Snap:
    rsi_14: float | None = None
    macd_val: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    macd_histogram_prev: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    ema_12: float | None = None
    ema_26: float | None = None
    volume_ratio: float | None = None


@dataclass
class SR:
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    signal: str = "NONE"
    distance_to_support_pct: float | None = None
    distance_to_resistance_pct: float | None = None


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrame에 기술 지표 컬럼 추가."""
    c = df["close"]
    v = df["volume"]

    # RSI
    delta = c.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = macd_line - signal_line
    df["macd_hist_prev"] = df["macd_hist"].shift(1)

    # BB
    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_middle"] = sma20
    df["bb_lower"] = sma20 - 2 * std20
    df["sma_20"] = sma20
    df["sma_50"] = c.rolling(50).mean()
    df["ema_12"] = ema12
    df["ema_26"] = ema26

    # Volume ratio
    vol_avg20 = v.rolling(20).mean()
    df["vol_ratio"] = (v / vol_avg20).round(2)

    return df


def snap_at(df: pd.DataFrame, idx: int) -> Snap | None:
    if idx < 50:
        return None
    r = df.iloc[idx]
    if pd.isna(r.get("rsi")):
        return None
    return Snap(
        rsi_14=r["rsi"],
        macd_val=r["macd"],
        macd_signal=r["macd_signal"],
        macd_histogram=r["macd_hist"],
        macd_histogram_prev=r["macd_hist_prev"],
        bb_upper=r["bb_upper"],
        bb_middle=r["bb_middle"],
        bb_lower=r["bb_lower"],
        sma_20=r["sma_20"],
        sma_50=r["sma_50"],
        ema_12=r["ema_12"],
        ema_26=r["ema_26"],
        volume_ratio=r["vol_ratio"],
    )


def compute_sr(df: pd.DataFrame, idx: int, lookback: int = 100) -> SR:
    """idx 시점에서 lookback 봉으로 S/R 계산."""
    start = max(0, idx - lookback)
    sub = df.iloc[start : idx + 1]
    if len(sub) < 20:
        return SR()

    closes = sub["close"].values
    highs = sub["high"].values
    lows = sub["low"].values
    current = closes[-1]
    order = 5

    # Local extremes
    n = len(closes)
    min_idx, max_idx = [], []
    for i in range(order, n - order):
        if all(closes[i] <= closes[i - j] for j in range(1, order + 1)) and all(
            closes[i] <= closes[i + j] for j in range(1, order + 1)
        ):
            min_idx.append(i)
        if all(closes[i] >= closes[i - j] for j in range(1, order + 1)) and all(
            closes[i] >= closes[i + j] for j in range(1, order + 1)
        ):
            max_idx.append(i)

    sup_prices = [float(lows[i]) for i in min_idx]
    res_prices = [float(highs[i]) for i in max_idx]

    def cluster(prices, tol=0.5):
        if not prices:
            return []
        sp = sorted(prices)
        clusters = [[sp[0]]]
        for p in sp[1:]:
            avg = sum(clusters[-1]) / len(clusters[-1])
            if abs(p - avg) / avg * 100 <= tol:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [(sum(c) / len(c), len(c)) for c in clusters if len(c) >= 2]

    sup_clusters = cluster(sup_prices)
    res_clusters = cluster(res_prices)

    supports = [(p, t) for p, t in sup_clusters if p < current]
    resistances = [(p, t) for p, t in res_clusters if p > current]

    nearest_sup = max(supports, key=lambda x: x[0])[0] if supports else None
    nearest_res = min(resistances, key=lambda x: x[0])[0] if resistances else None

    dist_sup = (
        round((current - nearest_sup) / current * 100, 2) if nearest_sup else None
    )
    dist_res = (
        round((nearest_res - current) / current * 100, 2) if nearest_res else None
    )

    signal = "NONE"
    threshold = 0.3
    recent = closes[-3:] if len(closes) >= 3 else closes

    if nearest_sup and dist_sup is not None and dist_sup <= threshold:
        signal = "SUPPORT_BOUNCE" if recent[-1] > recent[-2] else "SUPPORT_BREAK"
    elif nearest_res and dist_res is not None and dist_res <= threshold:
        signal = "RESISTANCE_BREAK" if recent[-1] > recent[-2] else "RESISTANCE_REJECT"

    return SR(
        nearest_support=nearest_sup,
        nearest_resistance=nearest_res,
        signal=signal,
        distance_to_support_pct=dist_sup,
        distance_to_resistance_pct=dist_res,
    )


# ── 탑다운 전략 체크 함수들 ──────────────────────────────────────


def chk_sr_support_bounce(s, c, sr):
    return sr is not None and sr.signal == "SUPPORT_BOUNCE"


def chk_sr_resistance_break(s, c, sr):
    return sr is not None and sr.signal == "RESISTANCE_BREAK"


def chk_sr_resistance_reject(s, c, sr):
    return sr is not None and sr.signal == "RESISTANCE_REJECT"


def chk_sr_support_near(s, c, sr):
    return (
        sr is not None
        and sr.distance_to_support_pct is not None
        and sr.distance_to_support_pct <= 0.3
    )


def chk_macd_golden(s, c, sr):
    return (
        s.macd_histogram is not None
        and s.macd_histogram_prev is not None
        and s.macd_histogram > 0
        and s.macd_histogram_prev <= 0
    )


def chk_macd_dead(s, c, sr):
    return (
        s.macd_histogram is not None
        and s.macd_histogram_prev is not None
        and s.macd_histogram < 0
        and s.macd_histogram_prev >= 0
    )


def chk_macd_hist_rising(s, c, sr):
    return (
        s.macd_histogram is not None
        and s.macd_histogram_prev is not None
        and s.macd_histogram > s.macd_histogram_prev
    )


def chk_macd_hist_falling(s, c, sr):
    return (
        s.macd_histogram is not None
        and s.macd_histogram_prev is not None
        and s.macd_histogram < s.macd_histogram_prev
    )


def chk_bb_squeeze(s, c, sr):
    if s.bb_upper and s.bb_middle and s.bb_lower and s.bb_middle != 0:
        return (s.bb_upper - s.bb_lower) / s.bb_middle < 0.02
    return False


def chk_bb_expansion(s, c, sr):
    if s.bb_upper and s.bb_middle and s.bb_lower and s.bb_middle != 0:
        return (s.bb_upper - s.bb_lower) / s.bb_middle > 0.08
    return False


def chk_bb_lower_break(s, c, sr):
    return s.bb_lower is not None and c <= s.bb_lower


def chk_rsi_lt40(s, c, sr):
    return s.rsi_14 is not None and s.rsi_14 < 40


def chk_rsi_lt45(s, c, sr):
    return s.rsi_14 is not None and s.rsi_14 < 45


def chk_rsi_lt50(s, c, sr):
    return s.rsi_14 is not None and s.rsi_14 < 50


def chk_rsi_gt50(s, c, sr):
    return s.rsi_14 is not None and s.rsi_14 > 50


def chk_rsi_gt55(s, c, sr):
    return s.rsi_14 is not None and s.rsi_14 > 55


def chk_rsi_gt60(s, c, sr):
    return s.rsi_14 is not None and s.rsi_14 > 60


def chk_price_above_sma20(s, c, sr):
    return s.sma_20 is not None and c > s.sma_20


def chk_price_below_sma20(s, c, sr):
    return s.sma_20 is not None and c < s.sma_20


def chk_price_above_sma50(s, c, sr):
    return s.sma_50 is not None and c > s.sma_50


def chk_price_below_sma50(s, c, sr):
    return s.sma_50 is not None and c < s.sma_50


def chk_ema12_above_ema26(s, c, sr):
    return s.ema_12 is not None and s.ema_26 is not None and s.ema_12 > s.ema_26


# ── 전략 테이블 ──────────────────────────────────────────────────

BUY_4H = [
    ("sr_지지반등", 1.67, chk_sr_support_bounce),
    ("sr_저항돌파", 1.22, chk_sr_resistance_break),
    ("macd_골든크로스", 1.12, chk_macd_golden),
    ("sr_지지근접", 0.76, chk_sr_support_near),
    ("macd_hist_증가", 0.55, chk_macd_hist_rising),
    ("price>sma20", 0.37, chk_price_above_sma20),
    ("price>sma50", 0.35, chk_price_above_sma50),
    ("ema12>ema26", 0.22, chk_ema12_above_ema26),
    ("bb_squeeze", 0.20, chk_bb_squeeze),
]
SELL_4H = [
    ("macd_데드크로스", 1.10, chk_macd_dead),
    ("sr_저항거부", 0.73, chk_sr_resistance_reject),
    ("macd_hist_감소", 0.49, chk_macd_hist_falling),
    ("price<sma20", 0.30, chk_price_below_sma20),
    ("price<sma50", 0.28, chk_price_below_sma50),
]
BUY_1H = [
    ("sr_저항돌파", 0.48, chk_sr_resistance_break),
    ("bb_확장", 0.41, chk_bb_expansion),
    ("macd_골든크로스", 0.32, chk_macd_golden),
    ("macd_hist_증가", 0.27, chk_macd_hist_rising),
    ("sr_지지반등", 0.27, chk_sr_support_bounce),
]
SELL_1H = [
    ("macd_데드크로스", 0.25, chk_macd_dead),
    ("macd_hist_감소", 0.21, chk_macd_hist_falling),
]
TRIGGER_BUY_15M = [chk_rsi_lt50, chk_rsi_lt45, chk_rsi_lt40, chk_bb_lower_break, chk_macd_hist_rising]
TRIGGER_SELL_15M = [chk_rsi_gt50, chk_rsi_gt55, chk_rsi_gt60, chk_macd_hist_falling]

MAX_BUY_4H = sum(s for _, s, _ in BUY_4H)
MAX_SELL_4H = sum(s for _, s, _ in SELL_4H)
MAX_BUY_1H = sum(s for _, s, _ in BUY_1H)
MAX_SELL_1H = sum(s for _, s, _ in SELL_1H)

# ── 전략 파라미터 ────────────────────────────────────────────────

GAP_4H = 0.5
THRESHOLD_BUY = 2.5
THRESHOLD_SELL = 1.8
BOOST_WEIGHT = 0.4
COUNTER_WEIGHT = 0.2
MULT_MIN = 0.6
MULT_MAX = 1.5


# ── 전략 평가 ────────────────────────────────────────────────────


def evaluate_layer(snap, close, sr, buy_ind, sell_ind):
    buy_score, sell_score = 0.0, 0.0
    buy_hits, sell_hits = [], []
    for name, score, fn in buy_ind:
        if fn(snap, close, sr):
            buy_score += score
            buy_hits.append(name)
    for name, score, fn in sell_ind:
        if fn(snap, close, sr):
            sell_score += score
            sell_hits.append(name)
    return buy_score, sell_score, buy_hits, sell_hits


def gate_4h(buy_s, sell_s):
    if buy_s - sell_s >= GAP_4H and buy_s >= THRESHOLD_BUY:
        return "BUY"
    if sell_s - buy_s >= GAP_4H and sell_s >= THRESHOLD_SELL:
        return "SELL"
    return "HOLD"


def calc_multiplier(direction, buy_1h, sell_1h):
    if direction == "BUY":
        boost = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0
        counter = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0
    else:
        boost = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0
        counter = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0
    raw = 1.0 + boost * BOOST_WEIGHT - counter * COUNTER_WEIGHT
    return max(MULT_MIN, min(MULT_MAX, raw))


@dataclass
class Signal:
    dt: datetime
    market: str
    direction: str  # BUY / SELL
    confidence: float
    price: float
    buy_hits_4h: list[str]
    sell_hits_4h: list[str]
    buy_hits_1h: list[str]
    sell_hits_1h: list[str]
    trigger_15m: list[str]
    rsi_15m: float | None = None
    sr_signal_4h: str = "NONE"
    sr_signal_1h: str = "NONE"


@dataclass
class Trade:
    entry_dt: datetime
    exit_dt: datetime | None = None
    market: str = ""
    side: str = ""  # BUY / SELL
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    confidence: float = 0.0
    indicators: dict = field(default_factory=dict)


# ── 매수/매도 시뮬레이터 ─────────────────────────────────────────


def find_4h_idx(df_4h: pd.DataFrame, target_dt: datetime) -> int | None:
    """15m 시점에 해당하는 최근 4H 봉 인덱스."""
    mask = df_4h["dt"] <= target_dt
    if mask.any():
        return mask.values.nonzero()[0][-1]
    return None


def find_1h_idx(df_1h: pd.DataFrame, target_dt: datetime) -> int | None:
    mask = df_1h["dt"] <= target_dt
    if mask.any():
        return mask.values.nonzero()[0][-1]
    return None


def run_backtest(
    market: str,
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    hold_bars: int = 16,  # 4시간 = 16 x 15m
) -> tuple[list[Signal], list[Trade]]:
    """탑다운 전략 백테스트. 시그널 발생 → hold_bars 후 청산."""
    signals: list[Signal] = []
    trades: list[Trade] = []
    cooldown_until = 0  # 중복 시그널 방지

    for i in range(50, len(df_15m)):
        if i < cooldown_until:
            continue

        dt_15m = df_15m.iloc[i]["dt"]
        close_15m = df_15m.iloc[i]["close"]

        # 4H 평가
        idx_4h = find_4h_idx(df_4h, dt_15m)
        if idx_4h is None or idx_4h < 50:
            continue
        snap_4h = snap_at(df_4h, idx_4h)
        if snap_4h is None:
            continue
        sr_4h = compute_sr(df_4h, idx_4h)
        buy_4h, sell_4h, bhits_4h, shits_4h = evaluate_layer(
            snap_4h, df_4h.iloc[idx_4h]["close"], sr_4h, BUY_4H, SELL_4H,
        )
        direction = gate_4h(buy_4h, sell_4h)
        if direction == "HOLD":
            continue

        # 1H 평가
        idx_1h = find_1h_idx(df_1h, dt_15m)
        if idx_1h is None or idx_1h < 50:
            continue
        snap_1h = snap_at(df_1h, idx_1h)
        sr_1h = compute_sr(df_1h, idx_1h)
        if snap_1h:
            buy_1h, sell_1h, bhits_1h, shits_1h = evaluate_layer(
                snap_1h, df_1h.iloc[idx_1h]["close"], sr_1h, BUY_1H, SELL_1H,
            )
            mult = calc_multiplier(direction, buy_1h, sell_1h)
        else:
            buy_1h, sell_1h = 0.0, 0.0
            bhits_1h, shits_1h = [], []
            mult = 1.0

        # 15m 트리거
        snap_15m = snap_at(df_15m, i)
        if snap_15m is None:
            continue
        trigger_fns = TRIGGER_BUY_15M if direction == "BUY" else TRIGGER_SELL_15M
        fired = [fn.__name__.replace("chk_", "") for fn in trigger_fns if fn(snap_15m, close_15m, None)]
        if not fired:
            continue

        # confidence 계산
        dir_score = buy_4h if direction == "BUY" else sell_4h
        max_4h = MAX_BUY_4H if direction == "BUY" else MAX_SELL_4H
        conf = min(dir_score / max_4h, 1.0) * mult if max_4h > 0 else 0
        conf = min(conf, 1.0)

        sig = Signal(
            dt=dt_15m,
            market=market,
            direction=direction,
            confidence=round(conf, 4),
            price=close_15m,
            buy_hits_4h=bhits_4h,
            sell_hits_4h=shits_4h,
            buy_hits_1h=bhits_1h,
            sell_hits_1h=shits_1h,
            trigger_15m=fired,
            rsi_15m=snap_15m.rsi_14,
            sr_signal_4h=sr_4h.signal,
            sr_signal_1h=sr_1h.signal if sr_1h else "NONE",
        )
        signals.append(sig)

        # 트레이드 시뮬레이션 (hold_bars 후 청산)
        exit_idx = min(i + hold_bars, len(df_15m) - 1)
        exit_price = df_15m.iloc[exit_idx]["close"]
        exit_dt = df_15m.iloc[exit_idx]["dt"]

        if direction == "BUY":
            pnl = (exit_price - close_15m) / close_15m * 100
        else:
            pnl = (close_15m - exit_price) / close_15m * 100

        trades.append(Trade(
            entry_dt=dt_15m,
            exit_dt=exit_dt,
            market=market,
            side=direction,
            entry_price=close_15m,
            exit_price=exit_price,
            pnl_pct=round(pnl, 4),
            confidence=round(conf, 4),
            indicators={
                "rsi_15m": snap_15m.rsi_14,
                "sr_4h": sr_4h.signal,
                "sr_1h": sr_1h.signal if sr_1h else "NONE",
                "buy_4h": bhits_4h,
                "sell_4h": shits_4h,
            },
        ))

        cooldown_until = i + 4  # 1시간 쿨다운

    return signals, trades


# ── 분석 리포트 ──────────────────────────────────────────────────


def analyze_trades(trades: list[Trade], label: str) -> dict:
    """거래 결과 분석."""
    if not trades:
        return {"label": label, "count": 0}

    buys = [t for t in trades if t.side == "BUY"]
    sells = [t for t in trades if t.side == "SELL"]
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    total_pnl = sum(t.pnl_pct for t in trades)
    avg_pnl = total_pnl / len(trades) if trades else 0

    return {
        "label": label,
        "count": len(trades),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 3),
        "avg_win": round(sum(t.pnl_pct for t in wins) / len(wins), 3) if wins else 0,
        "avg_loss": round(sum(t.pnl_pct for t in losses) / len(losses), 3) if losses else 0,
        "best": round(max(t.pnl_pct for t in trades), 2),
        "worst": round(min(t.pnl_pct for t in trades), 2),
    }


def analyze_indicator_accuracy(trades: list[Trade]) -> dict[str, dict]:
    """개별 지표가 활성화 되었을 때의 적중률 분석."""
    indicator_stats: dict[str, dict] = {}

    all_indicators = set()
    for t in trades:
        for hit in t.indicators.get("buy_4h", []):
            all_indicators.add(("BUY_4H", hit))
        for hit in t.indicators.get("sell_4h", []):
            all_indicators.add(("SELL_4H", hit))

    for group, name in all_indicators:
        key = f"{group}:{name}"
        if group == "BUY_4H":
            relevant = [t for t in trades if name in t.indicators.get("buy_4h", [])]
        else:
            relevant = [t for t in trades if name in t.indicators.get("sell_4h", [])]
        if not relevant:
            continue
        wins = [t for t in relevant if t.pnl_pct > 0]
        indicator_stats[key] = {
            "total": len(relevant),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(relevant) * 100, 1),
            "avg_pnl": round(sum(t.pnl_pct for t in relevant) / len(relevant), 3),
        }

    return indicator_stats


def analyze_rsi_ranges(trades: list[Trade]) -> dict[str, dict]:
    """RSI 구간별 적중률."""
    ranges = {
        "0-30": (0, 30), "30-40": (30, 40), "40-50": (40, 50),
        "50-60": (50, 60), "60-70": (60, 70), "70-100": (70, 100),
    }
    result = {}
    for label, (lo, hi) in ranges.items():
        subset = [
            t for t in trades
            if t.indicators.get("rsi_15m") is not None
            and lo <= t.indicators["rsi_15m"] < hi
        ]
        if not subset:
            continue
        wins = [t for t in subset if t.pnl_pct > 0]
        result[label] = {
            "total": len(subset),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(subset) * 100, 1),
            "avg_pnl": round(sum(t.pnl_pct for t in subset) / len(subset), 3),
        }
    return result


def analyze_sr_accuracy(trades: list[Trade]) -> dict[str, dict]:
    """S/R 시그널별 적중률."""
    result = {}
    for sr_key in ("sr_4h", "sr_1h"):
        signals_set = set(t.indicators.get(sr_key, "NONE") for t in trades)
        for sig in signals_set:
            subset = [t for t in trades if t.indicators.get(sr_key) == sig]
            if not subset:
                continue
            wins = [t for t in subset if t.pnl_pct > 0]
            result[f"{sr_key}:{sig}"] = {
                "total": len(subset),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(subset) * 100, 1),
                "avg_pnl": round(sum(t.pnl_pct for t in subset) / len(subset), 3),
            }
    return result


# ── 메인 ─────────────────────────────────────────────────────────


def print_report(
    market: str,
    stats: dict,
    ind_acc: dict,
    rsi_acc: dict,
    sr_acc: dict,
):
    coin = market.replace("KRW-", "")
    print(f"\n{'='*60}")
    print(f"  {coin} 백테스트 결과 ({stats['count']}건)")
    print(f"{'='*60}")

    if stats["count"] == 0:
        print("  시그널 없음")
        return

    print(f"  BUY: {stats['buy_count']}건 / SELL: {stats['sell_count']}건")
    print(f"  승률: {stats['win_rate']}% ({stats['win_count']}W / {stats['loss_count']}L)")
    print(f"  누적 수익률: {stats['total_pnl']:+.2f}%")
    print(f"  평균 수익률: {stats['avg_pnl']:+.3f}%")
    print(f"  평균 이익: {stats['avg_win']:+.3f}% / 평균 손실: {stats['avg_loss']:+.3f}%")
    print(f"  최대 이익: {stats['best']:+.2f}% / 최대 손실: {stats['worst']:+.2f}%")

    if ind_acc:
        print(f"\n  ── 지표별 적중률 ──")
        sorted_ind = sorted(ind_acc.items(), key=lambda x: x[1]["total"], reverse=True)
        for name, s in sorted_ind:
            print(f"  {name:30s}  {s['wins']:3d}/{s['total']:3d} ({s['win_rate']:5.1f}%)  avg={s['avg_pnl']:+.3f}%")

    if rsi_acc:
        print(f"\n  ── RSI 구간별 적중률 ──")
        for rng, s in sorted(rsi_acc.items()):
            print(f"  RSI {rng:8s}  {s['wins']:3d}/{s['total']:3d} ({s['win_rate']:5.1f}%)  avg={s['avg_pnl']:+.3f}%")

    if sr_acc:
        print(f"\n  ── S/R 시그널별 적중률 ──")
        sorted_sr = sorted(sr_acc.items(), key=lambda x: x[1]["total"], reverse=True)
        for name, s in sorted_sr:
            print(f"  {name:30s}  {s['wins']:3d}/{s['total']:3d} ({s['win_rate']:5.1f}%)  avg={s['avg_pnl']:+.3f}%")


def main():
    markets = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-DOGE"]
    all_trades: list[Trade] = []

    for market in markets:
        coin = market.replace("KRW-", "")
        print(f"\n[{coin}] 캔들 수집 중...")

        df_4h = fetch_candles(market, 240, days=400)
        print(f"  4H: {len(df_4h)}봉 ({df_4h.iloc[0]['dt'].strftime('%Y-%m-%d') if len(df_4h) else '-'} ~ {df_4h.iloc[-1]['dt'].strftime('%Y-%m-%d') if len(df_4h) else '-'})")

        df_1h = fetch_candles(market, 60, days=400)
        print(f"  1H: {len(df_1h)}봉")

        df_15m = fetch_candles(market, 15, days=400)
        print(f"  15m: {len(df_15m)}봉")

        # 지표 계산
        df_4h = compute_indicators(df_4h)
        df_1h = compute_indicators(df_1h)
        df_15m = compute_indicators(df_15m)

        # 백테스트 실행
        print(f"  전략 시뮬레이션 중...")
        signals, trades = run_backtest(market, df_15m, df_1h, df_4h)
        all_trades.extend(trades)

        # 분석
        stats = analyze_trades(trades, coin)
        ind_acc = analyze_indicator_accuracy(trades)
        rsi_acc = analyze_rsi_ranges(trades)
        sr_acc = analyze_sr_accuracy(trades)
        print_report(market, stats, ind_acc, rsi_acc, sr_acc)

    # 전체 종합
    print(f"\n{'='*60}")
    print(f"  전체 종합 ({len(all_trades)}건)")
    print(f"{'='*60}")
    total_stats = analyze_trades(all_trades, "TOTAL")
    total_ind = analyze_indicator_accuracy(all_trades)
    total_rsi = analyze_rsi_ranges(all_trades)
    total_sr = analyze_sr_accuracy(all_trades)
    print_report("전체", total_stats, total_ind, total_rsi, total_sr)


if __name__ == "__main__":
    main()
