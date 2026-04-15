"""탑다운 리포트 상세 — 최근 트레이드 + threshold 추천."""

from __future__ import annotations

from _topdown_trade import ClosedTrade


def print_recent_trades(trades: list[ClosedTrade]):
    """최근 20개 트레이드 출력."""
    print("\n  -- 최근 트레이드 (20개) --")
    print(
        f"  {'시각':<20} {'마켓':>8} {'방향':>4} {'점수':>5}"
        f" {'진입':>12} {'청산':>12} {'수익%':>7}"
        f" {'MFE':>6} {'MAE':>6} {'Q':>5}"
    )
    print(f"  {'─' * 100}")
    for t in trades[-20:]:
        mark = "O" if t.hit else "X"
        coin = t.market.replace("KRW-", "")
        print(
            f"  {t.entry_time[:19]:<20} {coin:>8} {t.direction:>4}"
            f" {t.entry_score:>5.1f}"
            f" {t.entry_price:>12,.0f} {t.exit_price:>12,.0f}"
            f" {t.profit_pct:>+6.2f}% {t.mfe_pct:>5.2f}%"
            f" {t.mae_pct:>5.2f}% {t.quality:>5.2f} {mark}"
        )


def print_recommendation(trades: list[ClosedTrade], thresholds: list[float]):
    """최적 threshold 추천."""
    print(f"\n{'=' * 60}")
    print("=== 최적 threshold 추천 ===")
    print(f"{'=' * 60}")
    best_th, best_score = 3.0, -999.0
    for th in thresholds:
        group = [t for t in trades if t.entry_score >= th]
        if len(group) < 5:
            continue
        acc = sum(1 for t in group if t.hit) / len(group)
        avg_q = sum(t.quality for t in group) / len(group)
        score = acc * 0.5 + avg_q * 0.3 + min(len(group) / 100, 1) * 0.2
        mark = " <-- BEST" if score > best_score else ""
        if score > best_score:
            best_score = score
            best_th = th
        print(
            f"  threshold {th}: {len(group)}건,"
            f" 적중 {acc*100:.1f}%, Q {avg_q:.2f},"
            f" score {score:.3f}{mark}"
        )
    print(f"\n  추천: threshold = {best_th}")
