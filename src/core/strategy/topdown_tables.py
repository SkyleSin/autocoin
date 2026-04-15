"""탑다운 MTF 전략 — TF별 지표 테이블 (백테스트 적중률 기반).

구조:
  4H: 방향 게이트 — BUY/SELL 점수 합산 → gap+threshold 충족 시 방향 결정
  1H: 신뢰도 부스트 — 4H 방향과 같은 방향 지표 점수 → multiplier 산출
  15m: 타이밍 트리거 — yes/no 게이트 (점수 없음, 조건 충족 시에만 진입)
"""

from __future__ import annotations

from .topdown_indicators import (
    chk_macd_dead,
    chk_macd_golden,
    chk_macd_hist_falling,
    chk_macd_hist_rising,
    chk_sr_resistance_break,
    chk_sr_resistance_reject,
    chk_sr_support_bounce,
    chk_sr_support_near,
)
from .topdown_indicators_bb import (
    chk_bb_expansion,
    chk_bb_lower_break,
    chk_bb_squeeze,
    chk_rsi_gt50,
    chk_rsi_gt55,
    chk_rsi_gt60,
    chk_rsi_lt40,
    chk_rsi_lt45,
    chk_rsi_lt50,
    chk_vol_low,
)
from .topdown_indicators_ma import (
    chk_ema12_above_ema26,
    chk_price_above_sma20,
    chk_price_above_sma50,
    chk_price_below_sma20,
    chk_price_below_sma50,
)

# 각 항목: (이름, score=(accuracy-50)/10, check_fn)

# ── 4H: 방향 게이트 ──
BUY_4H = [
    ("sr_지지반등", 1.67, chk_sr_support_bounce),
    ("sr_저항돌파", 1.22, chk_sr_resistance_break),
    ("macd_골든크로스", 1.12, chk_macd_golden),
    ("sr_지지근접_0.3%", 0.76, chk_sr_support_near),
    ("macd_hist_증가중", 0.55, chk_macd_hist_rising),
    ("price>sma20", 0.37, chk_price_above_sma20),
    ("price>sma50", 0.35, chk_price_above_sma50),
    ("ema12>ema26", 0.22, chk_ema12_above_ema26),
    ("bb_squeeze", 0.20, chk_bb_squeeze),
]

SELL_4H = [
    ("macd_데드크로스", 1.10, chk_macd_dead),
    ("sr_저항거부", 0.73, chk_sr_resistance_reject),
    ("macd_hist_감소중", 0.49, chk_macd_hist_falling),
    ("price<sma20", 0.30, chk_price_below_sma20),
    ("price<sma50", 0.28, chk_price_below_sma50),
    ("vol<0.5x", 0.22, chk_vol_low),
]

# ── 1H: 신뢰도 부스트 (4H 방향과 같은 방향 지표만 사용) ──
BUY_1H = [
    ("sr_저항돌파", 0.48, chk_sr_resistance_break),
    ("bb_확장", 0.41, chk_bb_expansion),
    ("macd_골든크로스", 0.32, chk_macd_golden),
    ("macd_hist_증가중", 0.27, chk_macd_hist_rising),
    ("sr_지지반등", 0.27, chk_sr_support_bounce),
]

SELL_1H = [
    ("macd_데드크로스", 0.25, chk_macd_dead),
    ("macd_hist_감소중", 0.21, chk_macd_hist_falling),
]

# ── 15m: 타이밍 트리거 (yes/no — 점수 없음) ──
# 4H 방향 결정 후 실제 진입 타이밍 게이트
# 4H 게이트가 이미 필터링하므로 15m은 완화된 조건 (백테스트 검증 기반)
TRIGGER_BUY_15M = [
    chk_rsi_lt50,              # RSI<50 (4H BUY 하에서 67.3% 적중)
    chk_rsi_lt45,              # RSI<45 (68.3% 적중)
    chk_rsi_lt40,              # RSI<40 (60.5% 적중)
    chk_bb_lower_break,        # BB 하단 이탈 (70.0% 적중)
    chk_macd_hist_rising,      # MACD 히스토그램 상승 (63.4% 적중)
]

TRIGGER_SELL_15M = [
    chk_rsi_gt50,              # RSI>50 (4H SELL 하에서 74.4% 적중)
    chk_rsi_gt55,              # RSI>55 (80.9% 적중)
    chk_rsi_gt60,              # RSI>60 (70.6% 적중)
    chk_macd_hist_falling,     # MACD 히스토그램 하락 (62.5% 적중)
]

# max possible scores (4H + 1H)
MAX_BUY_4H = sum(s for _, s, _ in BUY_4H)
MAX_SELL_4H = sum(s for _, s, _ in SELL_4H)
MAX_BUY_1H = sum(s for _, s, _ in BUY_1H)
MAX_SELL_1H = sum(s for _, s, _ in SELL_1H)

# legacy
MAX_BUY = MAX_BUY_4H + MAX_BUY_1H
MAX_SELL = MAX_SELL_4H + MAX_SELL_1H
