"""탑다운 MTF 전략 — TF별 지표 테이블 (백테스트 적중률 기반)."""

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
]

# ── 1H: 신뢰도 부스트 ──
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

# ── 15m: 타이밍 트리거 (yes/no) ──
TRIGGER_BUY_15M = [
    chk_rsi_lt50,
    chk_rsi_lt45,
    chk_rsi_lt40,
    chk_bb_lower_break,
    chk_macd_hist_rising,
]

TRIGGER_SELL_15M = [
    chk_rsi_gt50,
    chk_rsi_gt55,
    chk_rsi_gt60,
    chk_macd_hist_falling,
]

# ── MAX 점수 ──
MAX_BUY_4H = sum(s for _, s, _ in BUY_4H)
MAX_SELL_4H = sum(s for _, s, _ in SELL_4H)
MAX_BUY_1H = sum(s for _, s, _ in BUY_1H)
MAX_SELL_1H = sum(s for _, s, _ in SELL_1H)
