"""탑다운 MTF 전략 백테스트 — 2년치 BTC+ETH, 다중 청산 전략 비교.

사용법: python backtest.py
"""

from __future__ import annotations

import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

KST = timezone(timedelta(hours=9))
BITHUMB_URL = "https://api.bithumb.com"
CACHE_DIR = Path(".backtest_cache")

# ── 빗썸 캔들 수집 ──────────────────────────────────────────────


def fetch_candles(
    market: str, tf_minutes: int, days: int = 730, max_count: int = 200,
) -> pd.DataFrame:
    if tf_minutes >= 1440:
        url = f"{BITHUMB_URL}/v1/candles/days"
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
            time.sleep(0.12)

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


def fetch_or_cache(market: str, tf_minutes: int, days: int = 730) -> pd.DataFrame:
    """캐시된 DataFrame 로드 또는 수집."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{market.replace('-', '_')}_{tf_minutes}m_{days}d.pkl"
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                df = pickle.load(f)
            return df
        except Exception:
            pass

    df = fetch_candles(market, tf_minutes, days=days)
    with open(cache_file, "wb") as f:
        pickle.dump(df, f)
    return df


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
    atr_14: float | None = None  # ATR 추가


@dataclass
class SR:
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    signal: str = "NONE"
    distance_to_support_pct: float | None = None
    distance_to_resistance_pct: float | None = None


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    v = df["volume"]
    h = df["high"]
    low_s = df["low"]

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

    # ATR (14) — True Range EMA
    prev_close = c.shift(1)
    tr = pd.concat([
        (h - low_s),
        (h - prev_close).abs(),
        (low_s - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, min_periods=14).mean()
    df["atr_pct"] = (df["atr"] / c * 100).round(3)

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
        atr_14=r.get("atr"),
    )


def compute_sr(df: pd.DataFrame, idx: int, lookback: int = 100) -> SR:
    start = max(0, idx - lookback)
    sub = df.iloc[start: idx + 1]
    if len(sub) < 20:
        return SR()

    closes = sub["close"].values
    highs = sub["high"].values
    lows = sub["low"].values
    current = closes[-1]
    order = 5

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


# ── 체크 함수들 ─────────────────────────────────────────────────


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


def chk_macd_hist_negative(s, c, sr):
    return (
        s.macd_histogram is not None
        and s.macd_histogram_prev is not None
        and s.macd_histogram < 0
        and s.macd_histogram_prev < 0
    )


def chk_rsi_lt40_sell(s, c, sr):
    return s.rsi_14 is not None and s.rsi_14 < 40


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
    ("macd_골든크로스", 2.45, chk_macd_golden),
    ("bb_squeeze", 2.41, chk_bb_squeeze),
    ("price>sma20", 2.26, chk_price_above_sma20),
    ("macd_hist_증가중", 2.24, chk_macd_hist_rising),
    ("sr_지지근접_0.3%", 2.11, chk_sr_support_near),
    ("price>sma50", 2.10, chk_price_above_sma50),
    ("sr_지지반등", 2.07, chk_sr_support_bounce),
    ("ema12>ema26", 2.03, chk_ema12_above_ema26),
    ("sr_저항돌파", 1.89, chk_sr_resistance_break),
]
SELL_4H = [
    ("macd_hist_음수지속", 1.73, chk_macd_hist_negative),
    ("rsi<40", 1.71, chk_rsi_lt40_sell),
    ("price<sma50", 1.67, chk_price_below_sma50),
    ("sr_저항거부", 1.67, chk_sr_resistance_reject),
    ("price<sma20", 1.65, chk_price_below_sma20),
    ("macd_hist_감소중", 1.63, chk_macd_hist_falling),
    ("macd_데드크로스", 1.54, chk_macd_dead),
]
BUY_1H = [
    ("sr_지지반등", 3.36, chk_sr_support_bounce),
    ("sr_저항돌파", 2.84, chk_sr_resistance_break),
    ("bb_확장", 0.41, chk_bb_expansion),
    ("macd_골든크로스", 0.32, chk_macd_golden),
    ("macd_hist_증가중", 0.27, chk_macd_hist_rising),
]
SELL_1H = [
    ("sr_저항거부", 1.86, chk_sr_resistance_reject),
    ("macd_데드크로스", 0.25, chk_macd_dead),
    ("macd_hist_감소중", 0.21, chk_macd_hist_falling),
]

TRIGGER_BUY_15M_REQUIRED = [chk_macd_hist_rising]
TRIGGER_BUY_15M_OPTIONAL = [
    chk_rsi_lt50, chk_rsi_lt45, chk_rsi_lt40, chk_bb_lower_break,
]
TRIGGER_SELL_15M_REQUIRED = [chk_macd_hist_falling]
TRIGGER_SELL_15M_OPTIONAL = [chk_rsi_gt50, chk_rsi_gt55, chk_rsi_gt60]

MAX_BUY_4H = sum(s for _, s, _ in BUY_4H)
MAX_SELL_4H = sum(s for _, s, _ in SELL_4H)
MAX_BUY_1H = sum(s for _, s, _ in BUY_1H)
MAX_SELL_1H = sum(s for _, s, _ in SELL_1H)

GAP_4H = 1.52
THRESHOLD_BUY = 7.57
THRESHOLD_SELL = 6.0
BOOST_WEIGHT = 0.4
COUNTER_WEIGHT = 0.2
MULT_MIN = 0.6
MULT_MAX = 1.5
MACD_BLOCK = 50_000


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


def gate_4h(buy_s, sell_s, snap_4h=None):
    if snap_4h and snap_4h.macd_histogram is not None:
        macd_h = snap_4h.macd_histogram
        if macd_h < -MACD_BLOCK:
            if sell_s - buy_s >= GAP_4H and sell_s >= THRESHOLD_SELL:
                return "SELL"
            return "HOLD"
        if macd_h > MACD_BLOCK:
            if buy_s - sell_s >= GAP_4H and buy_s >= THRESHOLD_BUY:
                return "BUY"
            return "HOLD"

    if buy_s - sell_s >= GAP_4H and buy_s >= THRESHOLD_BUY:
        return "BUY"
    if sell_s - buy_s >= GAP_4H and sell_s >= THRESHOLD_SELL:
        return "SELL"
    return "HOLD"


def trigger_check_v2(snap, close, required_fns, optional_fns):
    fired = []
    for fn in required_fns:
        if fn(snap, close, None):
            fired.append(fn.__name__.replace("chk_", ""))
        else:
            return False, fired
    optional_fired = False
    for fn in optional_fns:
        if fn(snap, close, None):
            fired.append(fn.__name__.replace("chk_", ""))
            optional_fired = True
    return optional_fired, fired


def calc_multiplier(direction, buy_1h, sell_1h):
    if direction == "BUY":
        boost = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0
        counter = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0
    else:
        boost = sell_1h / MAX_SELL_1H if MAX_SELL_1H > 0 else 0
        counter = buy_1h / MAX_BUY_1H if MAX_BUY_1H > 0 else 0
    raw = 1.0 + boost * BOOST_WEIGHT - counter * COUNTER_WEIGHT
    return max(MULT_MIN, min(MULT_MAX, raw))


# ── 시그널 / 트레이드 데이터클래스 ──────────────────────────────


@dataclass
class Signal:
    dt: datetime
    idx_15m: int
    market: str
    direction: str
    confidence: float
    price: float
    atr_4h_pct: float | None = None  # 4H ATR% (청산 전략용)


@dataclass
class Trade:
    entry_dt: datetime
    exit_dt: datetime | None = None
    market: str = ""
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    path_quality: float = 0.0
    hold_bars: int = 0
    confidence: float = 0.0
    exit_reason: str = ""  # SL / TP / TRAILING / TIME_CAP / OPPOSITE / MAX_HOLD


# ── 청산 전략 설정 ───────────────────────────────────────────────


@dataclass
class ExitConfig:
    name: str = "baseline"
    # baseline / fixed / atr / trailing / time_cap / hybrid
    strategy: str = "baseline"

    # 공통
    max_hold_bars: int = 96  # 절대 최대 보유 (24시간)

    # 고정 SL/TP
    fixed_sl_pct: float = 0.0  # 0이면 비활성
    fixed_tp_pct: float = 0.0

    # ATR 기반
    atr_sl_mult: float = 0.0  # SL = ATR% × 배수
    atr_tp_mult: float = 0.0  # TP = ATR% × 배수

    # 트레일링
    breakeven_threshold: float = 0.0  # MFE 이 % 달성 시 본절 이동
    breakeven_buffer: float = 0.1  # 본절 + 슬리피지 여유
    trailing_threshold: float = 0.0  # MFE 이 % 달성 시 트레일링 활성
    trailing_atr_mult: float = 0.0  # 트레일링 스탑 폭 (ATR 기반)
    trailing_fixed_pct: float = 0.0  # 고정 % 트레일링 (ATR 대신)

    # 시간 캡 — 활성 시 trailing 미활성 상태면 강제 청산
    time_cap_bars: int = 0  # 0이면 비활성

    # 반대 시그널 청산 (baseline 전용)
    use_opposite_signal: bool = False


def make_configs() -> list[ExitConfig]:
    """최종 3가지 후보만."""
    return [
        ExitConfig(
            name="baseline (시그널→시그널)",
            strategy="baseline",
            max_hold_bars=96,
            use_opposite_signal=True,
        ),
        ExitConfig(
            name="fixed SL-1.5% / TP+3.0%",
            strategy="fixed",
            fixed_sl_pct=1.5,
            fixed_tp_pct=3.0,
            max_hold_bars=96,
        ),
        ExitConfig(
            name="하이브리드 (ATR SL + 본절 + 트레일 + 8h캡)",
            strategy="hybrid",
            atr_sl_mult=1.0,
            breakeven_threshold=1.0,
            trailing_threshold=2.0,
            trailing_atr_mult=0.5,
            time_cap_bars=32,
            max_hold_bars=96,
        ),
    ]


# ── 시그널 생성 ──────────────────────────────────────────────────


def find_tf_idx(df_tf: pd.DataFrame, target_dt: datetime) -> int | None:
    mask = df_tf["dt"] <= target_dt
    if mask.any():
        return mask.values.nonzero()[0][-1]
    return None


def generate_signals(
    market: str,
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
) -> list[Signal]:
    signals: list[Signal] = []
    cooldown_until = 0

    for i in range(50, len(df_15m)):
        if i < cooldown_until:
            continue

        dt_15m = df_15m.iloc[i]["dt"]
        close_15m = df_15m.iloc[i]["close"]

        idx_4h = find_tf_idx(df_4h, dt_15m)
        if idx_4h is None or idx_4h < 50:
            continue
        snap_4h = snap_at(df_4h, idx_4h)
        if snap_4h is None:
            continue
        sr_4h = compute_sr(df_4h, idx_4h)
        buy_4h, sell_4h, _, _ = evaluate_layer(
            snap_4h, df_4h.iloc[idx_4h]["close"], sr_4h, BUY_4H, SELL_4H,
        )
        direction = gate_4h(buy_4h, sell_4h, snap_4h)
        if direction == "HOLD":
            continue

        idx_1h = find_tf_idx(df_1h, dt_15m)
        if idx_1h is None or idx_1h < 50:
            continue
        snap_1h = snap_at(df_1h, idx_1h)
        sr_1h = compute_sr(df_1h, idx_1h)
        if snap_1h:
            buy_1h, sell_1h, _, _ = evaluate_layer(
                snap_1h, df_1h.iloc[idx_1h]["close"], sr_1h, BUY_1H, SELL_1H,
            )
        else:
            buy_1h, sell_1h = 0.0, 0.0

        snap_15m = snap_at(df_15m, i)
        if snap_15m is None:
            continue
        if direction == "BUY":
            ok, _ = trigger_check_v2(
                snap_15m, close_15m,
                TRIGGER_BUY_15M_REQUIRED, TRIGGER_BUY_15M_OPTIONAL,
            )
        else:
            ok, _ = trigger_check_v2(
                snap_15m, close_15m,
                TRIGGER_SELL_15M_REQUIRED, TRIGGER_SELL_15M_OPTIONAL,
            )
        if not ok:
            continue

        mult = calc_multiplier(direction, buy_1h, sell_1h)
        dir_score = buy_4h if direction == "BUY" else sell_4h
        max_4h = MAX_BUY_4H if direction == "BUY" else MAX_SELL_4H
        conf = min(dir_score / max_4h, 1.0) * mult if max_4h > 0 else 0
        conf = min(conf, 1.0)

        # 4H ATR% 추출
        atr_pct = df_4h.iloc[idx_4h].get("atr_pct")
        if pd.isna(atr_pct):
            atr_pct = None

        sig = Signal(
            dt=dt_15m,
            idx_15m=i,
            market=market,
            direction=direction,
            confidence=round(conf, 4),
            price=close_15m,
            atr_4h_pct=float(atr_pct) if atr_pct is not None else None,
        )
        signals.append(sig)
        cooldown_until = i + 4  # 1시간 쿨다운

    return signals


# ── 청산 시뮬레이션 ─────────────────────────────────────────────


def simulate_exit_long(
    entry_sig: Signal,
    df_15m: pd.DataFrame,
    config: ExitConfig,
    hard_exit_idx: int,
) -> tuple[int, float, str, float, float]:
    """현물 롱 포지션 청산 시뮬레이션 (BUY only, hard_exit_idx = 다음 SELL 시그널 idx).

    청산 조건 (가장 먼저 발동):
    - SL (초기 손절 / 본절 / 트레일링)
    - TP (고정/ATR)
    - 시간 캡 (trailing 미활성)
    - hard_exit_idx 도달 (다음 SELL 시그널 or max_hold)

    Returns: (exit_idx, exit_price, exit_reason, mfe_pct, mae_pct)
    """
    entry_idx = entry_sig.idx_15m
    entry_price = entry_sig.price
    atr_pct = entry_sig.atr_4h_pct or 2.0

    # 최대 보유 idx (max_hold vs hard_exit 중 먼저)
    max_idx = min(
        entry_idx + config.max_hold_bars,
        hard_exit_idx,
        len(df_15m) - 1,
    )

    # 초기 SL/TP
    sl_price: float | None = None
    tp_price: float | None = None

    if config.strategy == "fixed" and config.fixed_sl_pct > 0:
        sl_price = entry_price * (1 - config.fixed_sl_pct / 100)
        if config.fixed_tp_pct > 0:
            tp_price = entry_price * (1 + config.fixed_tp_pct / 100)

    if config.strategy in ("atr", "hybrid") and config.atr_sl_mult > 0:
        sl_pct = atr_pct * config.atr_sl_mult
        sl_price = entry_price * (1 - sl_pct / 100)

    if config.strategy == "atr" and config.atr_tp_mult > 0:
        tp_pct = atr_pct * config.atr_tp_mult
        tp_price = entry_price * (1 + tp_pct / 100)

    # baseline: SELL 시그널 or max_hold까지 홀딩
    if config.strategy == "baseline":
        mfe_pct = 0.0
        mae_pct = 0.0
        for j in range(entry_idx + 1, max_idx + 1):
            h = df_15m.iloc[j]["high"]
            low_val = df_15m.iloc[j]["low"]
            mfe_pct = max(mfe_pct, (h - entry_price) / entry_price * 100)
            mae_pct = max(mae_pct, (entry_price - low_val) / entry_price * 100)
        reason = "SELL_SIG" if max_idx == hard_exit_idx else "MAX_HOLD"
        return max_idx, df_15m.iloc[max_idx]["close"], reason, mfe_pct, mae_pct

    # 그 외 전략: 바 단위 체크
    breakeven_hit = False
    trailing_active = False
    peak_price = entry_price
    mfe_pct = 0.0
    mae_pct = 0.0

    for j in range(entry_idx + 1, max_idx + 1):
        bar = df_15m.iloc[j]
        h = bar["high"]
        low_val = bar["low"]
        close_val = bar["close"]
        bars_held = j - entry_idx

        current_mfe = (h - entry_price) / entry_price * 100
        current_mae = (entry_price - low_val) / entry_price * 100
        mfe_pct = max(mfe_pct, current_mfe)
        mae_pct = max(mae_pct, current_mae)

        # TP 체크
        if tp_price is not None and h >= tp_price:
            return j, tp_price, "TP", mfe_pct, mae_pct

        # SL 체크
        if sl_price is not None and low_val <= sl_price:
            reason = "BE" if breakeven_hit else ("TRAIL" if trailing_active else "SL")
            return j, sl_price, reason, mfe_pct, mae_pct

        # 본절 이동
        if (
            config.strategy in ("hybrid", "trailing")
            and not breakeven_hit
            and config.breakeven_threshold > 0
            and current_mfe >= config.breakeven_threshold
        ):
            be_price = entry_price * (1 + config.breakeven_buffer / 100)
            sl_price = max(sl_price, be_price) if sl_price is not None else be_price
            breakeven_hit = True

        # 트레일링
        if (
            config.strategy in ("hybrid", "trailing")
            and config.trailing_threshold > 0
        ):
            if not trailing_active and current_mfe >= config.trailing_threshold:
                trailing_active = True
                peak_price = h
            if trailing_active:
                peak_price = max(peak_price, h)
                trail_pct = (
                    atr_pct * config.trailing_atr_mult
                    if config.trailing_atr_mult > 0
                    else config.trailing_fixed_pct
                )
                new_sl = peak_price * (1 - trail_pct / 100)
                sl_price = max(sl_price, new_sl) if sl_price is not None else new_sl

        # 시간 캡
        if (
            config.time_cap_bars > 0
            and bars_held >= config.time_cap_bars
            and not trailing_active
        ):
            return j, close_val, "TIME_CAP", mfe_pct, mae_pct

    # max_idx 도달 (hard_exit_idx 또는 max_hold)
    reason = "SELL_SIG" if max_idx == hard_exit_idx else "MAX_HOLD"
    return max_idx, df_15m.iloc[max_idx]["close"], reason, mfe_pct, mae_pct


def run_realistic_spot_backtest(
    market: str,
    signals: list[Signal],
    df_15m: pd.DataFrame,
    config: ExitConfig,
) -> list[Trade]:
    """현물 롱 전용 백테스트 (상태 머신).

    - CASH 상태: BUY 시그널 발생 시 진입, SELL 시그널은 무시
    - LONG 상태: SL/TP/트레일링/시간캡/SELL시그널 중 먼저 발동되는 것으로 청산
    - LONG 상태에서 BUY 시그널 추가 발생 시 무시 (누적 금지)
    """
    trades: list[Trade] = []
    if not signals:
        return trades

    i = 0
    while i < len(signals):
        sig = signals[i]

        # CASH 상태 — BUY만 진입 트리거
        if sig.direction != "BUY":
            i += 1
            continue

        entry_sig = sig

        # 다음 SELL 시그널 찾기 (hard exit bound)
        hard_exit_idx = len(df_15m) - 1
        for j in range(i + 1, len(signals)):
            if (
                signals[j].direction == "SELL"
                and signals[j].idx_15m > entry_sig.idx_15m
            ):
                hard_exit_idx = signals[j].idx_15m
                break

        # 청산 시뮬레이션
        exit_idx, exit_price, reason, mfe, mae = simulate_exit_long(
            entry_sig, df_15m, config, hard_exit_idx,
        )

        pnl = (exit_price - entry_sig.price) / entry_sig.price * 100
        pq = (mfe - mae) / mfe if mfe > 0 else 0.0

        trades.append(Trade(
            entry_dt=entry_sig.dt,
            exit_dt=df_15m.iloc[exit_idx]["dt"],
            market=market,
            side="BUY",
            entry_price=entry_sig.price,
            exit_price=exit_price,
            pnl_pct=round(pnl, 4),
            mfe_pct=round(mfe, 4),
            mae_pct=round(mae, 4),
            path_quality=round(pq, 4),
            hold_bars=exit_idx - entry_sig.idx_15m,
            confidence=entry_sig.confidence,
            exit_reason=reason,
        ))

        # 청산 이후 시그널로 진행 (중복 진입 방지)
        i += 1
        while i < len(signals) and signals[i].idx_15m <= exit_idx:
            i += 1

    return trades


# ── 분석 ─────────────────────────────────────────────────────────


def analyze_trades(trades: list[Trade], label: str) -> dict:
    if not trades:
        return {"label": label, "count": 0}

    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    total_pnl = sum(t.pnl_pct for t in trades)
    avg_mfe = sum(t.mfe_pct for t in trades) / len(trades)
    avg_mae = sum(t.mae_pct for t in trades) / len(trades)
    avg_hold = sum(t.hold_bars for t in trades) / len(trades)

    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    profit_factor = (
        abs(sum(t.pnl_pct for t in wins) / sum(t.pnl_pct for t in losses))
        if losses and sum(t.pnl_pct for t in losses) != 0
        else float("inf")
    )

    # exit reason 분포
    reason_counts = {}
    for t in trades:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1

    # Drawdown (연속 손실 합계 최대)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.pnl_pct
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    return {
        "label": label,
        "count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(trades), 3),
        "avg_win": round(avg_win, 3),
        "avg_loss": round(avg_loss, 3),
        "profit_factor": round(profit_factor, 2),
        "payoff_ratio": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
        "best": round(max(t.pnl_pct for t in trades), 2),
        "worst": round(min(t.pnl_pct for t in trades), 2),
        "avg_mfe": round(avg_mfe, 3),
        "avg_mae": round(avg_mae, 3),
        "avg_hold": round(avg_hold, 1),
        "max_dd": round(max_dd, 2),
        "reasons": reason_counts,
    }


def print_comparison(results: list[dict]):
    """청산 전략 비교 테이블."""
    print(f"\n{'='*130}")
    print("  청산 전략 비교 (BTC+ETH 2년, 동일 시그널)")
    print(f"{'='*130}")
    print(
        f"  {'전략':<55s}  {'건수':>5s}  {'승률':>6s}  "
        f"{'평균PnL':>8s}  {'누적':>8s}  {'이익비':>6s}  "
        f"{'평균이익':>8s}  {'평균손실':>8s}  {'MDD':>7s}  {'보유h':>6s}"
    )
    print(f"  {'-' * 126}")
    for r in results:
        if r["count"] == 0:
            continue
        hold_h = r["avg_hold"] * 15 / 60
        print(
            f"  {r['label']:<55s}  {r['count']:>5d}  {r['win_rate']:>5.1f}%  "
            f"{r['avg_pnl']:>+7.3f}%  {r['total_pnl']:>+7.2f}%  "
            f"{r['payoff_ratio']:>6.2f}  "
            f"{r['avg_win']:>+7.3f}%  {r['avg_loss']:>+7.3f}%  "
            f"{r['max_dd']:>6.2f}%  {hold_h:>5.1f}h"
        )


def print_exit_reasons(results: list[dict]):
    """청산 사유 분포."""
    print(f"\n{'='*130}")
    print("  청산 사유 분포")
    print(f"{'='*130}")
    all_reasons = set()
    for r in results:
        all_reasons.update(r.get("reasons", {}).keys())
    reasons_sorted = sorted(all_reasons)

    header = "  전략".ljust(57) + "  " + "  ".join(
        f"{r:>8s}" for r in reasons_sorted
    )
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    for r in results:
        if r["count"] == 0:
            continue
        row = f"  {r['label']:<55s}"
        for reason in reasons_sorted:
            cnt = r.get("reasons", {}).get(reason, 0)
            pct = cnt / r["count"] * 100 if r["count"] > 0 else 0
            row += f"  {cnt:>3d} ({pct:>2.0f}%)"
        print(row)


# ── 메인 ─────────────────────────────────────────────────────────


def prepare_data(markets: list[str]) -> dict:
    """모든 마켓의 DataFrame + 시그널 준비 (캐시)."""
    data = {}
    for market in markets:
        coin = market.replace("KRW-", "")
        print(f"\n[{coin}] 데이터 준비 중...")

        df_4h = fetch_or_cache(market, 240, days=730)
        df_1h = fetch_or_cache(market, 60, days=730)
        df_15m = fetch_or_cache(market, 15, days=730)
        print(
            f"  4H: {len(df_4h)}봉 / 1H: {len(df_1h)}봉 / 15m: {len(df_15m)}봉 "
            f"({df_15m.iloc[0]['dt'].strftime('%Y-%m-%d')} ~ "
            f"{df_15m.iloc[-1]['dt'].strftime('%Y-%m-%d')})"
        )

        df_4h = compute_indicators(df_4h)
        df_1h = compute_indicators(df_1h)
        df_15m = compute_indicators(df_15m)

        print(f"  시그널 생성 중...")
        signals = generate_signals(market, df_15m, df_1h, df_4h)
        print(
            f"  시그널: {len(signals)}건 "
            f"(BUY {sum(1 for s in signals if s.direction == 'BUY')}"
            f" / SELL {sum(1 for s in signals if s.direction == 'SELL')})"
        )

        # ATR 통계
        atr_vals = [s.atr_4h_pct for s in signals if s.atr_4h_pct is not None]
        if atr_vals:
            print(
                f"  4H ATR%: 평균 {sum(atr_vals) / len(atr_vals):.3f}% "
                f"(min {min(atr_vals):.3f} / max {max(atr_vals):.3f})"
            )

        data[market] = {
            "df_15m": df_15m,
            "df_1h": df_1h,
            "df_4h": df_4h,
            "signals": signals,
        }
    return data


def main():
    markets = ["KRW-XRP", "KRW-SOL", "KRW-DOGE"]
    data = prepare_data(markets)

    configs = make_configs()

    print(f"\n{'='*130}")
    print(f"  {len(configs)}개 청산 전략 시뮬레이션 중 (현물 롱 전용 / BUY→SELL 페어)...")
    print(f"{'='*130}")

    all_results: list[dict] = []
    per_coin_results: dict[str, list[dict]] = {m: [] for m in markets}

    for config in configs:
        all_trades: list[Trade] = []
        for market in markets:
            d = data[market]
            trades = run_realistic_spot_backtest(
                market, d["signals"], d["df_15m"], config,
            )
            all_trades.extend(trades)
            per_coin_stats = analyze_trades(trades, f"{config.name}")
            per_coin_results[market].append(per_coin_stats)

        stats = analyze_trades(all_trades, config.name)
        all_results.append(stats)
        print(
            f"  ✓ {config.name}: "
            f"{stats['count']}건, 승률 {stats['win_rate']}%, "
            f"평균 {stats['avg_pnl']:+.3f}%, 누적 {stats['total_pnl']:+.2f}%"
        )

    # 종합 비교
    print_comparison(all_results)

    # 코인별 비교
    for market in markets:
        coin = market.replace("KRW-", "")
        print(f"\n\n  ── {coin} 단독 ──")
        print_comparison(per_coin_results[market])

    # 청산 사유 분포
    print_exit_reasons(all_results)

    # 최고 전략
    best = max(all_results, key=lambda x: x.get("avg_pnl", -999))
    worst = min(all_results, key=lambda x: x.get("avg_pnl", 999))
    print(f"\n{'='*130}")
    print(f"  🏆 최고 전략: {best['label']}")
    print(
        f"     평균수익 {best['avg_pnl']:+.3f}% "
        f"(baseline 대비 개선)"
    )
    print(f"  ⚠️  최저 전략: {worst['label']} ({worst['avg_pnl']:+.3f}%)")
    print(f"{'='*130}")


if __name__ == "__main__":
    main()
