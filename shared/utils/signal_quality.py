"""시그널 품질 점수 계산 — MFE/MAE 기반."""

from __future__ import annotations

# 품질 점수 가중치
_W_PROFIT = 0.4
_W_MFE_EFF = 0.3
_W_MAE_SAFE = 0.3
_MAE_THRESHOLD = 0.10


def calculate_quality(
    signal_type: str,
    entry_price: float,
    exit_price: float,
    mfe: float | None = None,
    mae: float | None = None,
) -> float | None:
    """MFE/MAE 기반 품질 점수 계산.

    quality = (수익률 점수 × 0.4) + (MFE 활용도 × 0.3) + (MAE 안정성 × 0.3)

    Returns:
        0.0~1.0 또는 None (계산 불가)
    """
    if entry_price <= 0 or exit_price <= 0:
        return None

    if mfe is None:
        mfe = exit_price
    if mae is None:
        mae = entry_price

    if signal_type == "BUY":
        profit_pct = (exit_price - entry_price) / entry_price
        mfe_pct = (mfe - entry_price) / entry_price
        mae_pct = (entry_price - mae) / entry_price
    else:  # SELL
        profit_pct = (entry_price - exit_price) / entry_price
        mfe_pct = (entry_price - mfe) / entry_price
        mae_pct = (mae - entry_price) / entry_price

    profit_score = max(0.0, min(1.0, (profit_pct + 0.05) / 0.15))

    if mfe_pct > 0:
        mfe_efficiency = max(0.0, min(1.0, profit_pct / mfe_pct))
    else:
        mfe_efficiency = 1.0 if profit_pct >= 0 else 0.0

    mae_safety = max(0.0, 1.0 - mae_pct / _MAE_THRESHOLD)

    quality = (
        profit_score * _W_PROFIT
        + mfe_efficiency * _W_MFE_EFF
        + mae_safety * _W_MAE_SAFE
    )
    return round(max(0.0, min(1.0, quality)), 4)
