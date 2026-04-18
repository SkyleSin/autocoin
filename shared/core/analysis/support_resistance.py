"""지지/저항선 자동 탐지.

핵심 원리 (이미지 4 참고):
- 2번 이상 반등하는 가격대 → 지지선 또는 저항선
- 지지선 반등 → 매수, 지지선 이탈 → 전환 (매도)
- 저항선 저항 → 매도, 저항선 돌파 → 전환 (매수)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PriceLevel:
    """지지/저항 가격대."""

    price: float
    level_type: str  # "support" | "resistance"
    touch_count: int  # 해당 가격대에 닿은 횟수
    strength: float  # 0.0 ~ 1.0


@dataclass(frozen=True)
class SRAnalysis:
    """지지/저항 분석 결과."""

    levels: list[PriceLevel]
    nearest_support: float | None
    nearest_resistance: float | None
    signal: str  # SUPPORT_BOUNCE|SUPPORT_BREAK|RESISTANCE_BREAK|RESISTANCE_REJECT|NONE
    distance_to_support_pct: float | None  # 현재가 대비 지지선까지 거리 (%)
    distance_to_resistance_pct: float | None

    def to_dict(self) -> dict:
        return {
            "levels": [
                {
                    "price": lv.price,
                    "type": lv.level_type,
                    "touches": lv.touch_count,
                    "strength": lv.strength,
                }
                for lv in self.levels
            ],
            "nearest_support": self.nearest_support,
            "nearest_resistance": self.nearest_resistance,
            "signal": self.signal,
            "distance_to_support_pct": self.distance_to_support_pct,
            "distance_to_resistance_pct": self.distance_to_resistance_pct,
        }


def _find_local_extremes(
    series: pd.Series, order: int = 5,
) -> tuple[list[int], list[int]]:
    """로컬 최솟값(지지 후보), 최댓값(저항 후보) 인덱스 반환.

    order: 양쪽으로 order개 봉보다 낮으면/높으면 극값으로 판정.
    """
    arr = series.values
    n = len(arr)
    minima: list[int] = []
    maxima: list[int] = []

    for i in range(order, n - order):
        if all(arr[i] <= arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] <= arr[i + j] for j in range(1, order + 1)
        ):
            minima.append(i)
        if all(arr[i] >= arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] >= arr[i + j] for j in range(1, order + 1)
        ):
            maxima.append(i)

    return minima, maxima


def _cluster_levels(
    prices: list[float], tolerance_pct: float = 0.5,
) -> list[tuple[float, int]]:
    """비슷한 가격대를 클러스터링. (평균 가격, 터치 횟수) 반환.

    tolerance_pct: 가격 차이가 이 비율 이내면 같은 클러스터.
    """
    if not prices:
        return []
    sorted_prices = sorted(prices)
    clusters: list[list[float]] = [[sorted_prices[0]]]

    for p in sorted_prices[1:]:
        cluster_avg = sum(clusters[-1]) / len(clusters[-1])
        if abs(p - cluster_avg) / cluster_avg * 100 <= tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    return [
        (sum(c) / len(c), len(c))
        for c in clusters
        if len(c) >= 2  # 2회 이상 터치한 가격대만
    ]


def analyze(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    order: int = 5,
    tolerance_pct: float = 0.5,
) -> SRAnalysis:
    """고가/저가/종가 시리즈로 지지/저항 분석.

    Args:
        highs: 고가 시리즈
        lows: 저가 시리즈
        closes: 종가 시리즈
        order: 극값 판정 윈도우 크기
        tolerance_pct: 클러스터링 허용 오차 (%)
    """
    if len(closes) < order * 2 + 1:
        return SRAnalysis([], None, None, "NONE", None, None)

    min_idx, max_idx = _find_local_extremes(closes, order)

    support_prices = [float(lows.iloc[i]) for i in min_idx]
    resistance_prices = [float(highs.iloc[i]) for i in max_idx]

    support_clusters = _cluster_levels(support_prices, tolerance_pct)
    resistance_clusters = _cluster_levels(resistance_prices, tolerance_pct)

    current_price = float(closes.iloc[-1])
    max_touches = max(
        (t for _, t in support_clusters + resistance_clusters),
        default=1,
    )

    levels: list[PriceLevel] = []
    for price, touches in support_clusters:
        if price < current_price:
            levels.append(PriceLevel(
                price=round(price, 2),
                level_type="support",
                touch_count=touches,
                strength=round(min(touches / max_touches, 1.0), 2),
            ))
    for price, touches in resistance_clusters:
        if price > current_price:
            levels.append(PriceLevel(
                price=round(price, 2),
                level_type="resistance",
                touch_count=touches,
                strength=round(min(touches / max_touches, 1.0), 2),
            ))

    supports = [lv for lv in levels if lv.level_type == "support"]
    resistances = [lv for lv in levels if lv.level_type == "resistance"]

    nearest_sup = max(supports, key=lambda x: x.price).price if supports else None
    nearest_res = (
        min(resistances, key=lambda x: x.price).price if resistances else None
    )

    dist_sup = (
        round((current_price - nearest_sup) / current_price * 100, 2)
        if nearest_sup
        else None
    )
    dist_res = (
        round((nearest_res - current_price) / current_price * 100, 2)
        if nearest_res
        else None
    )

    # 시그널 판단
    signal = "NONE"
    threshold_pct = 0.3  # 가격대에 0.3% 이내면 근접

    if nearest_sup and dist_sup is not None and dist_sup <= threshold_pct:
        # 최근 2봉이 반등 중이면 BOUNCE, 하락 중이면 BREAK
        recent = closes.iloc[-3:].values if len(closes) >= 3 else closes.values
        if len(recent) >= 2 and recent[-1] > recent[-2]:
            signal = "SUPPORT_BOUNCE"
        else:
            signal = "SUPPORT_BREAK"
    elif nearest_res and dist_res is not None and dist_res <= threshold_pct:
        recent = closes.iloc[-3:].values if len(closes) >= 3 else closes.values
        if len(recent) >= 2 and recent[-1] > recent[-2]:
            signal = "RESISTANCE_BREAK"
        else:
            signal = "RESISTANCE_REJECT"

    return SRAnalysis(
        levels=levels,
        nearest_support=nearest_sup,
        nearest_resistance=nearest_res,
        signal=signal,
        distance_to_support_pct=dist_sup,
        distance_to_resistance_pct=dist_res,
    )
