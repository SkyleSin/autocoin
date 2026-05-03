"""유저 손익 리포트 포맷터 — ct-scheduler, ct-telegram 공용."""

from __future__ import annotations

from infra.api.portfolio import get_user_portfolio
from infra.api.tracked_asset import get_active_markets
from infra.api.trade import get_trades


async def _build_price_map() -> dict[str, float]:
    """tracked_assets에서 현재가 맵 생성."""
    assets = await get_active_markets()
    return {
        a["market"].replace("KRW-", ""): a.get("current_price")
        for a in assets
    }


def _calc_coin_pnl(
    portfolio: list[dict], price_map: dict[str, float],
) -> tuple[float, float, list[str]]:
    """코인별 손익 계산 → (total_invested, total_current, coin_lines)."""
    total_invested = 0.0
    total_current = 0.0
    coin_lines: list[str] = []
    for item in portfolio:
        currency = item["currency"]
        if currency == "KRW":
            continue
        balance = float(item["balance"])
        avg_price = float(item.get("avg_buy_price", 0))
        if balance <= 0 or avg_price <= 0:
            continue
        current_price = price_map.get(currency)
        if not current_price:
            continue
        invested = avg_price * balance
        current_val = current_price * balance
        pnl = current_val - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0
        total_invested += invested
        total_current += current_val
        emoji = "🟢" if pnl >= 0 else "🔴"
        coin_lines.append(
            f"{emoji} `{currency}`: {pnl:+,.0f}원 ({pnl_pct:+.1f}%)"
            f" | 평균가 {avg_price:,.0f}"
        )
    return total_invested, total_current, coin_lines


def _format_trades(trades: list[dict]) -> list[str]:
    """최근 거래 텍스트 라인 생성."""
    lines: list[str] = []
    for t in trades:
        side_emoji = "🟢" if t.get("side") == "buy" else "🔴"
        created = t.get("created_at", "")[:16].replace("T", " ")
        lines.append(
            f"{side_emoji} `{t['market']}` {t['side']}"
            f" p={t['price_krw']} [{t['status']}] {created}"
        )
    return lines


async def build_user_report(
    user_id: int, price_map: dict[str, float] | None = None,
) -> str | None:
    """유저 손익 리포트 텍스트. 데이터 없으면 None.

    price_map이 None이면 tracked_assets에서 현재가를 내부 조회한다.
    """
    portfolio = await get_user_portfolio(user_id)
    if not portfolio:
        return None
    if price_map is None:
        price_map = await _build_price_map()

    total_invested, total_current, coin_lines = _calc_coin_pnl(
        portfolio, price_map,
    )
    total_pnl = total_current - total_invested
    total_pnl_pct = (
        (total_pnl / total_invested * 100) if total_invested > 0 else 0
    )
    lines = ["📊 *손익 리포트*\n"]
    lines.append(f"💰 총 평가액: {total_current:,.0f}원")
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    lines.append(
        f"{pnl_emoji} 총 손익: {total_pnl:+,.0f}원 ({total_pnl_pct:+.1f}%)\n"
    )
    if coin_lines:
        lines.append("*코인별 손익:*")
        lines.extend(coin_lines)

    trades = await get_trades(limit=5, user_id=user_id)
    if trades:
        lines.append("\n*최근 거래:*")
        lines.extend(_format_trades(trades))
    return "\n".join(lines)
