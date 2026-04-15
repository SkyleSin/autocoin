"""탑다운 백테스트 데이터 수집 유틸."""

from __future__ import annotations

import time

import pandas as pd

from bithumb.candle import BithumbCandle

WINDOW = 200


def fetch_candles_minutes(
    client: BithumbCandle, market: str, unit: int, total: int,
) -> list[dict]:
    """분봉 캔들 페이지네이션 수집."""
    all_c: list[dict] = []
    to_param: str | None = None
    while len(all_c) < total:
        batch_size = min(200, total - len(all_c))
        batch = client.get_minutes(unit, market, count=batch_size, to=to_param)
        if not batch:
            break
        all_c.extend(batch)
        to_param = batch[-1]["candle_date_time_utc"]
        time.sleep(0.12)
    all_c.sort(key=lambda c: c["candle_date_time_utc"])
    seen: set[tuple] = set()
    return [
        c for c in all_c
        if (k := (c["market"], c["candle_date_time_utc"])) not in seen
        and not seen.add(k)  # type: ignore[func-returns-value]
    ]


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    """캔들 딕셔너리 리스트를 DataFrame으로 변환."""
    df = pd.DataFrame(candles)
    df["open_at"] = pd.to_datetime(df["candle_date_time_utc"], utc=True)
    df = df.sort_values("open_at").reset_index(drop=True)
    for col, src in [
        ("open", "opening_price"), ("high", "high_price"),
        ("low", "low_price"), ("close", "trade_price"),
        ("volume", "candle_acc_trade_volume"),
    ]:
        df[col] = df[src].astype(float)
    return df


def compute_at(df: pd.DataFrame, idx: int):
    """df의 특정 위치에서 지표+S/R 계산."""
    from core.analysis.indicators import compute_all
    from core.analysis.support_resistance import analyze as analyze_sr

    start = max(0, idx - WINDOW + 1)
    w = df.iloc[start:idx + 1]
    if len(w) < 26:
        return None, None, 0.0
    closes = pd.Series(w["close"].values)
    snap = compute_all(closes, pd.Series(w["volume"].values))
    if snap is None:
        return None, None, 0.0
    sr = analyze_sr(
        pd.Series(w["high"].values), pd.Series(w["low"].values), closes,
    )
    return snap, sr, float(closes.iloc[-1])


def find_tf_idx(df_ref: pd.DataFrame, target_time) -> int | None:
    """target_time 이전의 최신 캔들 인덱스."""
    mask = df_ref["open_at"] <= target_time
    if not mask.any():
        return None
    return int(mask.values.nonzero()[0][-1])
