"""탑다운 백테스트 결과 출력 — 지표 통계 + 전략 요약."""

from __future__ import annotations

from _topdown_report_detail import print_recent_trades, print_recommendation
from _topdown_trade import ClosedTrade

THRESHOLDS = [2.0, 2.5, 3.0, 3.5, 4.0]


def print_indicator_stats(trades: list[ClosedTrade]):
    """각 지표별 적중률/수익/MFE/MAE 집계."""
    print(f"\n{'=' * 90}")
    print("=== 전체 지표 적중률 (시그널 전환 기준) ===")
    print(f"{'=' * 90}")

    stats: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        for f in t.fired:
            stats.setdefault(f, []).append(t)

    rows = []
    for name, ts in stats.items():
        if len(ts) < 5:
            continue
        hits = sum(1 for t in ts if t.hit)
        avg_p = sum(t.profit_pct for t in ts) / len(ts)
        avg_mfe = sum(t.mfe_pct for t in ts) / len(ts)
        avg_mae = sum(t.mae_pct for t in ts) / len(ts)
        rows.append((name, len(ts), hits / len(ts) * 100, avg_p, avg_mfe, avg_mae))

    rows.sort(key=lambda r: r[2], reverse=True)
    print(
        f"  {'지표':<36} {'건수':>5} {'적중%':>6}"
        f" {'평균수익':>8} {'MFE':>6} {'MAE':>6}"
    )
    print(f"  {'─' * 72}")
    for name, cnt, acc, ap, am, ae in rows:
        mark = "*" if acc >= 55 else " "
        print(
            f"  {name:<36} {cnt:>5} {acc:>5.1f}%{mark}"
            f" {ap:>+7.3f}% {am:>5.2f}% {ae:>5.2f}%"
        )


def print_strategy_results(trades: list[ClosedTrade], months: int):
    """탑다운 전략 시뮬레이션 결과."""
    print(f"\n{'=' * 90}")
    print("=== 탑다운 전략 시뮬레이션 ===")
    print(f"{'=' * 90}")

    if not trades:
        print("  트레이드 없음")
        return

    _print_summary(trades, "Overall", months)

    print(f"\n  {'─' * 50}")
    print("  -- Threshold별 성과 --")
    for th in THRESHOLDS:
        group = [t for t in trades if t.entry_score >= th]
        if group:
            _print_summary(group, f"score>={th}", months)

    print(f"\n  {'─' * 50}")
    print("  -- 마켓별 성과 --")
    for mkt in sorted({t.market for t in trades}):
        group = [t for t in trades if t.market == mkt]
        _print_summary(group, mkt, months)

    print_recent_trades(trades)
    print_recommendation(trades, THRESHOLDS)


def _print_summary(trades: list[ClosedTrade], label: str, months: int):
    n = len(trades)
    hits = sum(1 for t in trades if t.hit)
    avg_p = sum(t.profit_pct for t in trades) / n
    total_p = sum(t.profit_pct for t in trades)
    avg_mfe = sum(t.mfe_pct for t in trades) / n
    avg_mae = sum(t.mae_pct for t in trades) / n
    avg_q = sum(t.quality for t in trades) / n
    print(
        f"  [{label}] {n}건 | 적중 {hits/n*100:.1f}%"
        f" | 평균 {avg_p:+.3f}% | 누적 {total_p:+.1f}%"
        f" | MFE {avg_mfe:.2f}% MAE {avg_mae:.2f}%"
        f" | Q {avg_q:.2f} | 월 {n/max(months,1):.1f}건"
    )
