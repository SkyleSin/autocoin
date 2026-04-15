"""포지션 트래커 — 시그널 전환 기반 평가 + MFE/MAE + 품질 점수."""

from __future__ import annotations

from dataclasses import dataclass, field

from _topdown_trade import ClosedTrade


@dataclass
class OpenPosition:
    """추적 중인 가상 포지션."""
    market: str
    direction: str  # BUY | SELL
    entry_price: float
    entry_time: str
    entry_score: float
    fired: list[str]
    confidence: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0

    def __post_init__(self):
        self.mfe = self.entry_price
        self.mae = self.entry_price

    def update(self, high: float, low: float):
        """캔들의 고/저가로 MFE/MAE 갱신."""
        if self.direction == "BUY":
            self.mfe = max(self.mfe, high)
            self.mae = min(self.mae, low)
        else:
            self.mfe = min(self.mfe, low)
            self.mae = max(self.mae, high)


@dataclass
class PositionTracker:
    """마켓별 오픈 포지션 관리 + 청산 기록."""
    positions: dict[str, OpenPosition] = field(default_factory=dict)
    trades: list[ClosedTrade] = field(default_factory=list)

    def on_signal(
        self, market: str, direction: str,
        price: float, time_str: str, score: float, fired: list[str],
        confidence: float = 0.0,
    ):
        """시그널 발생 시 포지션 열기/닫기."""
        key = market
        if key in self.positions:
            pos = self.positions[key]
            if pos.direction != direction:
                self._close(pos, price, time_str)
                del self.positions[key]
            else:
                return  # 같은 방향 — 무시

        if direction in ("BUY", "SELL"):
            self.positions[key] = OpenPosition(
                market=market, direction=direction,
                entry_price=price, entry_time=time_str,
                entry_score=score, fired=fired,
                confidence=confidence,
            )

    def update_all(self, market: str, high: float, low: float):
        """매 캔들마다 MFE/MAE 갱신."""
        if market in self.positions:
            self.positions[market].update(high, low)

    def _close(self, pos: OpenPosition, exit_price: float, exit_time: str):
        self.trades.append(ClosedTrade(
            market=pos.market, direction=pos.direction,
            entry_price=pos.entry_price, exit_price=exit_price,
            entry_time=pos.entry_time, exit_time=exit_time,
            entry_score=pos.entry_score, fired=pos.fired,
            mfe_price=pos.mfe, mae_price=pos.mae,
        ))
