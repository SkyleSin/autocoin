"""추세선 기반 차트 패턴 인식 (교과서적 정의).

패턴 분류:
- 구조적: 쌍봉, 쌍바닥, 삼중천정, 트리플바닥, 머리어깨, 역머리어깨
- 추세선: 상승삼각형, 하락삼각형, 대칭삼각형, 상승쐐기, 하락쐐기, 박스권
- 깃발: 상승깃발, 하락깃발
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# ── 활성 패턴 화이트리스트 (백테스트 적중률 50%+ 기반) ──
_ACTIVE_PATTERNS: set[str] = {
    "double_top",           # 쌍봉 67%
    "triple_top",           # 삼중천정 50%+
    "triple_bottom",        # 트리플바닥 55%+
    "head_and_shoulders",   # 머리어깨 56%
    "falling_wedge",        # 하락쐐기 70%
    "bull_flag",            # 상승깃발 58%
    "bear_flag",            # 하락깃발 80%
    "box_range",            # 박스권 (HOLD, 정보용)
    "symmetric_triangle",   # 대칭삼각형 (HOLD, 정보용)
}

# ── 데이터 클래스 ──


@dataclass(frozen=True)
class Trendline:
    """추세선 (최소자승법 피팅)."""

    slope: float
    intercept: float
    start_idx: int
    end_idx: int

    def price_at(self, idx: int) -> float:
        return self.slope * idx + self.intercept

    def to_dict(self) -> dict:
        return {
            "start_idx": self.start_idx,
            "end_idx": self.end_idx,
            "start_price": self.price_at(self.start_idx),
            "end_price": self.price_at(self.end_idx),
        }


@dataclass(frozen=True)
class PatternMatch:
    """탐지된 패턴."""

    name: str
    name_ko: str
    signal: str      # "BUY" | "SELL" | "HOLD"
    confidence: int  # 50~100
    description: str
    active: bool = True  # 백테스트 적중률 50%+ 기반 활성 여부


@dataclass(frozen=True)
class PatternAnalysis:
    """패턴 분석 결과."""

    patterns: list[PatternMatch]
    resistance_line: Trendline | None = None
    support_line: Trendline | None = None

    def to_dict(self) -> dict:
        result: dict = {
            "patterns": [
                {
                    "name": p.name,
                    "name_ko": p.name_ko,
                    "signal": p.signal,
                    "confidence": p.confidence,
                    "description": p.description,
                    "active": p.active,
                }
                for p in self.patterns
            ],
        }
        if self.resistance_line:
            result["resistance_trendline"] = self.resistance_line.to_dict()
        if self.support_line:
            result["support_trendline"] = self.support_line.to_dict()
        return result


# ── 유틸리티 ──


def _find_peaks(series: pd.Series, order: int = 5) -> list[int]:
    """로컬 최댓값 인덱스. 양쪽에 strictly 낮은 값이 있어야 함."""
    arr = series.values
    n = len(arr)
    peaks = []
    for i in range(order, n - order):
        if all(arr[i] > arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] > arr[i + j] for j in range(1, order + 1)
        ):
            peaks.append(i)
    return peaks


def _find_valleys(series: pd.Series, order: int = 5) -> list[int]:
    """로컬 최솟값 인덱스. 양쪽에 strictly 높은 값이 있어야 함."""
    arr = series.values
    n = len(arr)
    valleys = []
    for i in range(order, n - order):
        if all(arr[i] < arr[i - j] for j in range(1, order + 1)) and all(
            arr[i] < arr[i + j] for j in range(1, order + 1)
        ):
            valleys.append(i)
    return valleys


def _similar(a: float, b: float, tol_pct: float = 1.5) -> bool:
    """두 가격이 tol_pct% 이내로 비슷한지."""
    if a == 0:
        return False
    return abs(a - b) / a * 100 <= tol_pct


def _order_for_timeframe(timeframe: str) -> int:
    """타임프레임별 피크/밸리 탐색 order."""
    return {
        "1m": 15, "3m": 12, "5m": 10, "15m": 10,
        "30m": 8, "1h": 5, "4h": 5, "1d": 5,
    }.get(timeframe, 10)


def _min_swing_pct(highs: pd.Series, lows: pd.Series) -> float:
    """고저 변동폭(%)."""
    highest, lowest = float(highs.max()), float(lows.min())
    if lowest == 0:
        return 0.0
    return (highest - lowest) / lowest * 100


def _fit_trendline(indices: list[int], prices: list[float]) -> Trendline | None:
    """최소자승법으로 추세선 피팅. 최소 2점 필요."""
    n = len(indices)
    if n < 2:
        return None
    x_mean = sum(indices) / n
    y_mean = sum(prices) / n
    numer = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(indices, prices, strict=True)
    )
    denom = sum((x - x_mean) ** 2 for x in indices)
    if denom == 0:
        return None
    slope = numer / denom
    intercept = y_mean - slope * x_mean
    return Trendline(slope=slope, intercept=intercept,
                     start_idx=indices[0], end_idx=indices[-1])


def _slope_pct(line: Trendline, avg_price: float) -> float:
    """추세선 기울기를 봉당 가격변동률(%)로 환산."""
    if avg_price == 0:
        return 0.0
    return line.slope / avg_price * 100


# ── 구조적 패턴 (고점/저점 + 넥라인 이탈 확인) ──


def _detect_double_top(
    highs: pd.Series, closes: pd.Series, order: int,
) -> PatternMatch | None:
    """쌍봉 — 고점 2회 + 넥라인 이탈 시 매도."""
    peaks = _find_peaks(highs, order)
    if len(peaks) < 2:
        return None
    p1, p2 = peaks[-2], peaks[-1]
    h1, h2 = float(highs.iloc[p1]), float(highs.iloc[p2])
    if not _similar(h1, h2, 1.5):
        return None
    neckline = float(closes.iloc[p1:p2 + 1].min())
    neckline_drop = (h1 - neckline) / h1 * 100
    if neckline_drop < 1.5:
        return None
    current = float(closes.iloc[-1])
    if current < neckline:
        return PatternMatch(
            "double_top", "쌍봉", "SELL", 100,
            f"고점 {h1:,.0f}↔{h2:,.0f}, 넥라인 {neckline:,.0f} 이탈",
        )
    return None


def _detect_double_bottom(
    lows: pd.Series, closes: pd.Series, order: int,
) -> PatternMatch | None:
    """쌍바닥 — 저점 2회 + 넥라인 돌파 시 매수."""
    valleys = _find_valleys(lows, order)
    if len(valleys) < 2:
        return None
    v1, v2 = valleys[-2], valleys[-1]
    l1, l2 = float(lows.iloc[v1]), float(lows.iloc[v2])
    if not _similar(l1, l2, 1.5):
        return None
    neckline = float(closes.iloc[v1:v2 + 1].max())
    neckline_rise = (neckline - l1) / l1 * 100
    if neckline_rise < 1.5:
        return None
    current = float(closes.iloc[-1])
    if current > neckline:
        return PatternMatch(
            "double_bottom", "쌍바닥", "BUY", 80,
            f"저점 {l1:,.0f}↔{l2:,.0f}, 넥라인 {neckline:,.0f} 돌파",
        )
    return None


def _detect_triple_top(
    highs: pd.Series, closes: pd.Series, order: int,
) -> PatternMatch | None:
    """삼중천정 — 고점 3회 + 넥라인 이탈 시 매도."""
    peaks = _find_peaks(highs, order)
    if len(peaks) < 3:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1, h2, h3 = float(highs.iloc[p1]), float(highs.iloc[p2]), float(highs.iloc[p3])
    if not (_similar(h1, h2, 1.5) and _similar(h2, h3, 1.5)):
        return None
    # 넥라인 = 골짜기 2개 중 높은 값
    valley_between_1 = float(closes.iloc[p1:p2 + 1].min())
    valley_between_2 = float(closes.iloc[p2:p3 + 1].min())
    neckline = max(valley_between_1, valley_between_2)
    neckline_drop = (h1 - neckline) / h1 * 100
    if neckline_drop < 1.5:
        return None
    current = float(closes.iloc[-1])
    if current < neckline:
        return PatternMatch(
            "triple_top", "삼중천정", "SELL", 100,
            f"고점 3회 저항 ({h1:,.0f}), 넥라인 {neckline:,.0f} 이탈",
        )
    return None


def _detect_triple_bottom(
    lows: pd.Series, closes: pd.Series, order: int,
) -> PatternMatch | None:
    """트리플바닥 — 저점 3회 + 넥라인 돌파 시 매수."""
    valleys = _find_valleys(lows, order)
    if len(valleys) < 3:
        return None
    v1, v2, v3 = valleys[-3], valleys[-2], valleys[-1]
    l1, l2, l3 = float(lows.iloc[v1]), float(lows.iloc[v2]), float(lows.iloc[v3])
    if not (_similar(l1, l2, 1.5) and _similar(l2, l3, 1.5)):
        return None
    peak_between_1 = float(closes.iloc[v1:v2 + 1].max())
    peak_between_2 = float(closes.iloc[v2:v3 + 1].max())
    neckline = min(peak_between_1, peak_between_2)
    neckline_rise = (neckline - l1) / l1 * 100
    if neckline_rise < 1.5:
        return None
    current = float(closes.iloc[-1])
    if current > neckline:
        return PatternMatch(
            "triple_bottom", "트리플바닥", "BUY", 80,
            f"저점 3회 지지 ({l1:,.0f}), 넥라인 {neckline:,.0f} 돌파",
        )
    return None


def _detect_head_and_shoulders(
    highs: pd.Series, closes: pd.Series, order: int,
) -> PatternMatch | None:
    """머리어깨 — 넥라인 이탈 시 매도."""
    peaks = _find_peaks(highs, order)
    if len(peaks) < 3:
        return None
    p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
    h1, h2, h3 = float(highs.iloc[p1]), float(highs.iloc[p2]), float(highs.iloc[p3])
    if not (h2 > h1 and h2 > h3 and _similar(h1, h3, 2.0)):
        return None
    head_ratio = (h2 - max(h1, h3)) / h2 * 100
    if head_ratio < 1.0:
        return None
    # 넥라인 = 어깨 사이 골짜기 중 높은 값
    valley_l = float(closes.iloc[p1:p2 + 1].min())
    valley_r = float(closes.iloc[p2:p3 + 1].min())
    neckline = max(valley_l, valley_r)
    current = float(closes.iloc[-1])
    if current < neckline:
        return PatternMatch(
            "head_and_shoulders", "머리어깨", "SELL", 90,
            f"머리 {h2:,.0f}, 어깨 {h1:,.0f}↔{h3:,.0f}, 넥라인 {neckline:,.0f} 이탈",
        )
    return None


def _detect_inverse_head_and_shoulders(
    lows: pd.Series, closes: pd.Series, order: int,
) -> PatternMatch | None:
    """역머리어깨 — 넥라인 돌파 시 매수."""
    valleys = _find_valleys(lows, order)
    if len(valleys) < 3:
        return None
    v1, v2, v3 = valleys[-3], valleys[-2], valleys[-1]
    l1, l2, l3 = float(lows.iloc[v1]), float(lows.iloc[v2]), float(lows.iloc[v3])
    if not (l2 < l1 and l2 < l3 and _similar(l1, l3, 2.0)):
        return None
    head_ratio = (min(l1, l3) - l2) / min(l1, l3) * 100
    if head_ratio < 1.0:
        return None
    peak_l = float(closes.iloc[v1:v2 + 1].max())
    peak_r = float(closes.iloc[v2:v3 + 1].max())
    neckline = min(peak_l, peak_r)
    current = float(closes.iloc[-1])
    if current > neckline:
        return PatternMatch(
            "inverse_head_and_shoulders", "역머리어깨", "BUY", 90,
            f"역머리 {l2:,.0f}, 어깨 {l1:,.0f}↔{l3:,.0f}, 넥라인 {neckline:,.0f} 돌파",
        )
    return None


# ── 추세선 기반 패턴 (기울기 조합) ──


_FLAT_THRESHOLD = 0.01  # 봉당 0.01% 미만이면 수평


def _classify_trendline_patterns(
    res_line: Trendline,
    sup_line: Trendline,
    closes: pd.Series,
    avg_price: float,
) -> list[PatternMatch]:
    """저항/지지 추세선 기울기 조합으로 패턴 분류."""
    res_pct = _slope_pct(res_line, avg_price)
    sup_pct = _slope_pct(sup_line, avg_price)

    res_flat = abs(res_pct) < _FLAT_THRESHOLD
    sup_flat = abs(sup_pct) < _FLAT_THRESHOLD
    res_rising = res_pct >= _FLAT_THRESHOLD
    res_falling = res_pct <= -_FLAT_THRESHOLD
    sup_rising = sup_pct >= _FLAT_THRESHOLD
    sup_falling = sup_pct <= -_FLAT_THRESHOLD

    # 수렴 여부: 끝 갭 < 시작 갭의 70%
    gap_start = res_line.price_at(res_line.start_idx) - sup_line.price_at(sup_line.start_idx)
    gap_end = res_line.price_at(res_line.end_idx) - sup_line.price_at(sup_line.end_idx)
    converging = gap_start > 0 and gap_end < gap_start * 0.7

    current = float(closes.iloc[-1])
    last_idx = len(closes) - 1
    res_current = res_line.price_at(last_idx)
    sup_current = sup_line.price_at(last_idx)

    patterns: list[PatternMatch] = []

    # 상승삼각형: 저항 수평 + 지지 상승 + 상단 돌파
    if res_flat and sup_rising:
        if current > res_current:
            patterns.append(PatternMatch(
                "ascending_triangle", "상승삼각형", "BUY", 65,
                f"저항 {res_current:,.0f} 수평, 저점 상승 → 돌파",
            ))

    # 하락삼각형: 저항 하락 + 지지 수평 + 하단 이탈
    elif res_falling and sup_flat:
        if current < sup_current:
            patterns.append(PatternMatch(
                "descending_triangle", "하락삼각형", "SELL", 65,
                f"지지 {sup_current:,.0f} 수평, 고점 하락 → 이탈",
            ))

    # 대칭삼각형: 저항 하락 + 지지 상승 + 수렴
    elif res_falling and sup_rising and converging:
        patterns.append(PatternMatch(
            "symmetric_triangle", "대칭삼각형", "HOLD", 50,
            "고점 하락 + 저점 상승 → 수렴 중, 방향 미확정",
        ))

    # 상승쐐기: 비활성화 — 백테스트 적중률 0~23%
    elif res_rising and sup_rising and converging:
        pass  # noqa: SIM108

    # 하락쐐기: 저항·지지 모두 하락 + 수렴 → 상단 돌파 시 매수
    elif res_falling and sup_falling and converging:
        if current > res_current:
            patterns.append(PatternMatch(
                "falling_wedge", "하락쐐기형", "BUY", 75,
                "고점·저점 하락하며 수렴, 상단 돌파 → 상승 전환",
            ))

    # 박스권: 저항·지지 모두 수평
    elif res_flat and sup_flat:
        patterns.append(PatternMatch(
            "box_range", "박스권횡보", "HOLD", 50,
            f"저항 {res_current:,.0f} ~ 지지 {sup_current:,.0f} 박스권",
        ))

    return patterns


# ── 깃발 패턴 ──


def _detect_bull_flag(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    peaks: list[int], valleys: list[int],
) -> PatternMatch | None:
    """상승깃발 — 급등(깃대) 후 소폭 하락 평행채널(깃발)."""
    if not peaks or not valleys:
        return None
    pole_top_idx = peaks[-1]
    pole_top = float(highs.iloc[pole_top_idx])
    # 깃대 시작: 깃대 꼭대기 이전의 가장 가까운 저점
    pre_valleys = [v for v in valleys if v < pole_top_idx]
    if not pre_valleys:
        return None
    pole_bottom_idx = pre_valleys[-1]
    pole_bottom = float(lows.iloc[pole_bottom_idx])
    pole_gain = (pole_top - pole_bottom) / pole_bottom * 100
    if pole_gain < 1.0:
        return None
    # 깃발: 깃대 꼭대기 이후 데이터
    flag_data = closes.iloc[pole_top_idx:]
    if len(flag_data) < 3:
        return None
    flag_start = float(flag_data.iloc[0])
    flag_end = float(flag_data.iloc[-1])
    flag_change = (flag_end - flag_start) / flag_start * 100
    # 깃발: 소폭 하락 (-3% ~ +0.5%), 되돌림 50% 이내
    if not (-3.0 <= flag_change <= 0.5):
        return None
    flag_low = float(lows.iloc[pole_top_idx:].min())
    retracement = (pole_top - flag_low) / (pole_top - pole_bottom)
    if retracement > 0.5:
        return None
    return PatternMatch(
        "bull_flag", "상승깃발", "BUY", 80,
        f"급등 {pole_gain:.1f}% 후 조정 {flag_change:.1f}%, 재상승 예상",
    )


def _detect_bear_flag(
    highs: pd.Series, lows: pd.Series, closes: pd.Series,
    peaks: list[int], valleys: list[int],
) -> PatternMatch | None:
    """하락깃발 — 급락(깃대) 후 소폭 반등 평행채널(깃발)."""
    if not peaks or not valleys:
        return None
    pole_bottom_idx = valleys[-1]
    pole_bottom = float(lows.iloc[pole_bottom_idx])
    # 깃대 시작: 깃대 바닥 이전의 가장 가까운 고점
    pre_peaks = [p for p in peaks if p < pole_bottom_idx]
    if not pre_peaks:
        return None
    pole_top_idx = pre_peaks[-1]
    pole_top = float(highs.iloc[pole_top_idx])
    pole_drop = (pole_bottom - pole_top) / pole_top * 100
    if pole_drop > -1.0:
        return None
    # 깃발: 깃대 바닥 이후 데이터
    flag_data = closes.iloc[pole_bottom_idx:]
    if len(flag_data) < 3:
        return None
    flag_start = float(flag_data.iloc[0])
    flag_end = float(flag_data.iloc[-1])
    flag_change = (flag_end - flag_start) / flag_start * 100
    # 깃발: 소폭 반등 (-0.5% ~ +3%), 되돌림 50% 이내
    if not (-0.5 <= flag_change <= 3.0):
        return None
    flag_high = float(highs.iloc[pole_bottom_idx:].max())
    retracement = (flag_high - pole_bottom) / (pole_top - pole_bottom)
    if retracement > 0.5:
        return None
    return PatternMatch(
        "bear_flag", "하락깃발", "SELL", 80,
        f"급락 {pole_drop:.1f}% 후 반등 {flag_change:.1f}%, 재하락 예상",
    )


# ── 메인 ──


def _filter_conflicting(patterns: list[PatternMatch]) -> list[PatternMatch]:
    """활성 패턴 BUY/SELL 동시 → 활성 BUY/SELL 제거, 비활성은 유지."""
    active_signals = {p.signal for p in patterns if p.active}
    if "BUY" in active_signals and "SELL" in active_signals:
        return [
            p for p in patterns
            if not p.active or p.signal == "HOLD"
        ]
    return patterns


def analyze(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    order: int | None = None,
    *,
    timeframe: str = "15m",
    min_swing_pct: float = 2.0,
) -> PatternAnalysis:
    """OHLC 데이터에서 차트 패턴 탐지 (추세선 기반).

    Returns:
        PatternAnalysis (패턴 목록 + 저항/지지 추세선)
    """
    if len(closes) < 30:
        return PatternAnalysis([])

    # 최소 변동폭 필터
    swing = _min_swing_pct(highs, lows)
    if swing < min_swing_pct:
        return PatternAnalysis([])

    if order is None:
        order = _order_for_timeframe(timeframe)

    peaks = _find_peaks(highs, order)
    valleys = _find_valleys(lows, order)

    # 추세선 피팅
    res_line = _fit_trendline(
        peaks, [float(highs.iloc[p]) for p in peaks],
    ) if len(peaks) >= 2 else None

    sup_line = _fit_trendline(
        valleys, [float(lows.iloc[v]) for v in valleys],
    ) if len(valleys) >= 2 else None

    patterns: list[PatternMatch] = []
    avg_price = float(closes.mean())

    # 1) 구조적 패턴 (고점/저점 + 넥라인)
    # 비활성화: 쌍바닥(0%), 역머리어깨(0~33%) — 백테스트 적중률 기반
    structural_detectors = [
        lambda: _detect_double_top(highs, closes, order),
        lambda: _detect_triple_top(highs, closes, order),
        lambda: _detect_triple_bottom(lows, closes, order),
        lambda: _detect_head_and_shoulders(highs, closes, order),
    ]
    for detect in structural_detectors:
        result = detect()
        if result is not None:
            patterns.append(result)

    # 2) 추세선 기반 패턴 (기울기 조합)
    if res_line and sup_line:
        patterns.extend(
            _classify_trendline_patterns(res_line, sup_line, closes, avg_price),
        )

    # 3) 깃발 패턴 (급등/급락 + 채널)
    bull = _detect_bull_flag(highs, lows, closes, peaks, valleys)
    if bull:
        patterns.append(bull)
    bear = _detect_bear_flag(highs, lows, closes, peaks, valleys)
    if bear:
        patterns.append(bear)

    # 활성 여부 마킹 (비활성 패턴도 데이터 수집 목적으로 유지)
    patterns = [
        PatternMatch(
            p.name, p.name_ko, p.signal, p.confidence, p.description,
            active=(p.name in _ACTIVE_PATTERNS),
        )
        for p in patterns
    ]

    # 상충 패턴 발생 시 order 확대 재시도 (활성 패턴만 기준)
    active_signals = {p.signal for p in patterns if p.active}
    if "BUY" in active_signals and "SELL" in active_signals and order < 20:
        return analyze(
            highs, lows, closes, order=order + 5,
            timeframe=timeframe, min_swing_pct=min_swing_pct,
        )
    patterns = _filter_conflicting(patterns)
    patterns.sort(key=lambda p: p.confidence, reverse=True)

    return PatternAnalysis(
        patterns=patterns,
        resistance_line=res_line,
        support_line=sup_line,
    )
