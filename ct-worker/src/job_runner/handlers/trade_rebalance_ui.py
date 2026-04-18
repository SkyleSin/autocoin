"""리밸런싱 UI 헬퍼 — 메시지 텍스트 + 인라인 버튼 생성."""

from __future__ import annotations

from typing import Any


def build_rebalance_message(
    buy_market: str,
    buy_confidence: float,
    intended_krw: float,
    weakest: dict[str, Any],
    hold_value: float,
    recommended_pct: int,
    conf_diff: float,
    rec_sell_value: float,
    rec_fee: float,
    rec_net: float,
) -> list[str]:
    """리밸런싱 안내 메시지 라인 리스트."""
    return [
        "\U0001f504 *리밸런싱 기회 감지*\n",
        f"*신규 매수:* `{buy_market}` (신뢰도 {buy_confidence:.0%})",
        f"필요 금액: `{intended_krw:,.0f}원`\n",
        f"*매도 후보:* `{weakest['market']}`",
        f"  보유: `{weakest['balance']:.4f}` {weakest['currency']}",
        f"  평가: `{hold_value:,.0f}원`",
        f"  신뢰도: {weakest['confidence']:.0%} ({weakest['signal']})\n",
        f"*추천: {recommended_pct}% 매도* (신뢰도 차이 +{conf_diff:.0%})",
        f"  매도 금액: `{rec_sell_value:,.0f}원`",
        f"  왕복 수수료: `{rec_fee:,.0f}원`",
        f"  순 투자 가능: `{rec_net:,.0f}원`",
    ]


def build_rebalance_buttons(
    weakest: dict[str, Any],
    buy_market: str,
    buy_price: float,
    recommended_pct: int,
) -> dict[str, list]:
    """리밸런싱 인라인 키보드 버튼."""
    choices = [25, 50, 75, 100]
    row: list[dict[str, str]] = []
    for pct in choices:
        star = " \u2b50" if pct == recommended_pct else ""
        row.append({
            "text": f"{'\U0001f504 ' if pct == recommended_pct else ''}{pct}%{star}",
            "callback_data": (
                f"rebalance:{weakest['market']}:{buy_market}:{buy_price}:{pct}"
            ),
        })
    return {
        "inline_keyboard": [
            row[:2],
            row[2:],
            [{"text": "\u23ed\ufe0f 유지", "callback_data": f"trade_pass:{buy_market}"}],
        ],
    }
