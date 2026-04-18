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
# 2026-04-19 백테스트 실측 기반 업데이트 (BTC/ETH/XRP/DOGE 1년, 1586건)

# ── 4H: 방향 게이트 ──
BUY_4H = [
    ("bb_squeeze", 2.43, chk_bb_squeeze),           # 74.3% (152건)
    ("macd_골든크로스", 2.33, chk_macd_golden),        # 73.3% (330건)
    ("macd_hist_증가중", 2.13, chk_macd_hist_rising),  # 71.3% (724건)
    ("price>sma20", 2.10, chk_price_above_sma20),    # 71.0% (637건)
    ("price>sma50", 2.02, chk_price_above_sma50),    # 70.2% (707건)
    ("ema12>ema26", 2.00, chk_ema12_above_ema26),    # 70.0% (853건)
    ("sr_저항돌파", 1.96, chk_sr_resistance_break),    # 69.6% (204건)
    ("sr_지지근접_0.3%", 1.74, chk_sr_support_near),   # 67.4% (319건)
    ("sr_지지반등", 1.73, chk_sr_support_bounce),      # 67.3% (294건)
]

SELL_4H = [
    ("macd_데드크로스", 1.81, chk_macd_dead),          # 68.1% (791건)
    ("price<sma50", 1.80, chk_price_below_sma50),    # 68.0% (879건)
    ("price<sma20", 1.77, chk_price_below_sma20),    # 67.7% (949건)
    ("macd_hist_감소중", 1.71, chk_macd_hist_falling),  # 67.1% (862건)
    ("sr_저항거부", 1.58, chk_sr_resistance_reject),    # 65.8% (38건)
]

# ── 1H: 신뢰도 부스트 (1H S/R 실측 기반) ──
BUY_1H = [
    ("sr_지지반등", 3.55, chk_sr_support_bounce),      # 85.5% (62건)
    ("sr_저항돌파", 2.50, chk_sr_resistance_break),     # 75.0% (96건)
    ("bb_확장", 0.41, chk_bb_expansion),
    ("macd_골든크로스", 0.32, chk_macd_golden),
    ("macd_hist_증가중", 0.27, chk_macd_hist_rising),
]

SELL_1H = [
    ("sr_저항거부", 1.30, chk_sr_resistance_reject),    # 63.0% (54건)
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
