"""탑다운 MTF 전략 — TF별 지표 테이블 (백테스트 적중률 기반)."""

from __future__ import annotations

from .topdown_indicators import (
    chk_macd_dead,
    chk_macd_golden,
    chk_macd_hist_falling,
    chk_macd_hist_negative,
    chk_macd_hist_rising,
    chk_rsi_lt40_sell,
    chk_sr_resistance_break,
    chk_sr_resistance_reject,
    chk_sr_support_bounce,
    chk_sr_support_near,
)
from .topdown_indicators_bb import (
    chk_bb_expansion,
    chk_bb_lower_break,
    chk_bb_squeeze,
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
# 2026-04-20 백테스트 실측 기반 업데이트 (BTC+ETH 1년, 2050건)

# ── 4H: 방향 게이트 ──
BUY_4H = [
    ("macd_골든크로스", 2.45, chk_macd_golden),        # 74.5% (204건)
    ("bb_squeeze", 2.41, chk_bb_squeeze),           # 74.1% (174건)
    ("price>sma20", 2.26, chk_price_above_sma20),    # 72.6% (413건)
    ("macd_hist_증가중", 2.24, chk_macd_hist_rising),  # 72.4% (492건)
    ("sr_지지근접_0.3%", 2.11, chk_sr_support_near),   # 71.1% (232건)
    ("price>sma50", 2.10, chk_price_above_sma50),    # 71.0% (497건)
    ("sr_지지반등", 2.07, chk_sr_support_bounce),      # 70.7% (205건)
    ("ema12>ema26", 2.03, chk_ema12_above_ema26),    # 70.3% (609건)
    ("sr_저항돌파", 1.89, chk_sr_resistance_break),    # 68.9% (148건)
]

SELL_4H = [
    ("macd_hist_음수지속", 1.73, chk_macd_hist_negative),  # 67.3% (1360건)
    ("rsi<40", 1.71, chk_rsi_lt40_sell),              # 67.1% (1275건)
    ("price<sma50", 1.67, chk_price_below_sma50),    # 66.7% (1553건)
    ("sr_저항거부", 1.67, chk_sr_resistance_reject),    # 66.7% (87건)
    ("price<sma20", 1.65, chk_price_below_sma20),    # 66.5% (1637건)
    ("macd_hist_감소중", 1.63, chk_macd_hist_falling),  # 66.3% (1558건)
    ("macd_데드크로스", 1.54, chk_macd_dead),          # 65.4% (315건)
]

# ── 1H: 신뢰도 부스트 (1H S/R 실측 기반) ──
BUY_1H = [
    ("sr_지지반등", 3.36, chk_sr_support_bounce),      # 83.6% (67건)
    ("sr_저항돌파", 2.84, chk_sr_resistance_break),     # 78.4% (74건)
    ("bb_확장", 0.41, chk_bb_expansion),
    ("macd_골든크로스", 0.32, chk_macd_golden),
    ("macd_hist_증가중", 0.27, chk_macd_hist_rising),
]

SELL_1H = [
    ("sr_저항거부", 1.86, chk_sr_resistance_reject),    # 68.6% (35건)
    ("macd_데드크로스", 0.25, chk_macd_dead),
    ("macd_hist_감소중", 0.21, chk_macd_hist_falling),
]

# ── 15m: 타이밍 트리거 (yes/no) ──
# required: 반드시 충족해야 하는 트리거 (모멘텀 반전 확인)
# optional: 하나라도 충족하면 보조 조건 만족
TRIGGER_BUY_15M_REQUIRED = [chk_macd_hist_rising]
TRIGGER_BUY_15M_OPTIONAL = [
    chk_rsi_lt50,
    chk_rsi_lt45,
    chk_rsi_lt40,
    chk_bb_lower_break,
]

TRIGGER_SELL_15M_REQUIRED = [chk_macd_hist_falling]
TRIGGER_SELL_15M_OPTIONAL = [
    chk_rsi_gt60,
]

# ── MAX 점수 ──
MAX_BUY_4H = sum(s for _, s, _ in BUY_4H)
MAX_SELL_4H = sum(s for _, s, _ in SELL_4H)
MAX_BUY_1H = sum(s for _, s, _ in BUY_1H)
MAX_SELL_1H = sum(s for _, s, _ in SELL_1H)
