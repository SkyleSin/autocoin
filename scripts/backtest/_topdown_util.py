"""탑다운 백테스트 공용 유틸리티."""

from __future__ import annotations


def add_score(
    name: str, score: float, direction: str,
    buy: float, sell: float, fired: list[str],
) -> tuple[float, float]:
    """지표 발화 시 점수 가산 + 로그."""
    fired.append(f"{direction}:{name}({score:.2f})")
    if direction == "BUY":
        buy += score
    else:
        sell += score
    return buy, sell


def clamp(v: float, lo: float, hi: float) -> float:
    """값을 lo~hi 범위로 제한."""
    return max(lo, min(hi, v))
