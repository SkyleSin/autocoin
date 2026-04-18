"""기술 지표 계산 — pandas 기반, 외부 라이브러리 없이 직접 구현."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class IndicatorSnapshot:
    """한 시점의 기술 지표 모음."""

    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    macd_histogram_prev: float | None  # 직전 봉 히스토그램 (크로스 전환 감지용)
    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    sma_20: float | None
    sma_50: float | None
    ema_12: float | None
    ema_26: float | None
    volume_ratio: float | None  # 현재 거래량 / 20봉 평균 거래량 (1.0 = 평균)

    def to_dict(self) -> dict[str, float | None]:
        return {
            "rsi_14": self.rsi_14,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "macd_histogram_prev": self.macd_histogram_prev,
            "bb_upper": self.bb_upper,
            "bb_middle": self.bb_middle,
            "bb_lower": self.bb_lower,
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "ema_12": self.ema_12,
            "ema_26": self.ema_26,
            "volume_ratio": self.volume_ratio,
        }


def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Relative Strength Index)."""
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def sma(series: pd.Series, period: int) -> pd.Series:
    """단순 이동평균 (Simple Moving Average)."""
    return series.rolling(window=period).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """지수 이동평균 (Exponential Moving Average)."""
    return series.ewm(span=span, adjust=False).mean()


def macd(
    closes: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD 라인, 시그널 라인, 히스토그램 반환."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(
    closes: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """볼린저밴드 (상단, 중간, 하단) 반환."""
    middle = sma(closes, period)
    std = closes.rolling(window=period).std()
    upper = middle + (std * num_std)
    lower = middle - (std * num_std)
    return upper, middle, lower


def compute_all(
    closes: pd.Series, volumes: pd.Series | None = None,
) -> IndicatorSnapshot | None:
    """종가(+거래량) 시리즈로 모든 지표를 계산해 최신 값의 스냅샷 반환.

    데이터가 부족하면 None.
    """
    if len(closes) < 26:
        return None

    rsi_series = rsi(closes)
    macd_line, signal_line, histogram = macd(closes)
    bb_upper, bb_middle, bb_lower = bollinger_bands(closes)
    sma_20 = sma(closes, 20)
    sma_50 = sma(closes, 50) if len(closes) >= 50 else pd.Series([None] * len(closes))
    ema_12 = ema(closes, 12)
    ema_26 = ema(closes, 26)

    # 거래량 비율: 현재 거래량 / 20봉 평균
    vol_ratio: float | None = None
    if volumes is not None and len(volumes) >= 20:
        avg_vol = float(sma(volumes, 20).iloc[-1])
        cur_vol = float(volumes.iloc[-1])
        if avg_vol > 0:
            vol_ratio = round(cur_vol / avg_vol, 2)

    def _last(s: pd.Series, offset: int = -1) -> float | None:
        v = s.iloc[offset]
        if pd.isna(v):
            return None
        return round(float(v), 4)

    return IndicatorSnapshot(
        rsi_14=_last(rsi_series),
        macd=_last(macd_line),
        macd_signal=_last(signal_line),
        macd_histogram=_last(histogram),
        macd_histogram_prev=_last(histogram, -2) if len(histogram) >= 2 else None,
        bb_upper=_last(bb_upper),
        bb_middle=_last(bb_middle),
        bb_lower=_last(bb_lower),
        sma_20=_last(sma_20),
        sma_50=_last(sma_50),
        ema_12=_last(ema_12),
        ema_26=_last(ema_26),
        volume_ratio=vol_ratio,
    )
