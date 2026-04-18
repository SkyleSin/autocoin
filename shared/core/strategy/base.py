"""매매 전략 베이스 클래스 및 전략 레지스트리."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.config import Settings


@dataclass(frozen=True)
class StrategyResult:
    """전략 평가 결과."""

    signal_type: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 ~ 1.0
    reasons: list[str]
    metadata: dict | None = None  # 전략별 추가 정보


class BaseStrategy(ABC):
    """매매 전략 인터페이스."""

    name: str
    description: str

    @abstractmethod
    async def evaluate(
        self,
        market: str,
        settings: Settings,
    ) -> StrategyResult:
        """시그널 판단. API를 통해 지표를 조회."""
        ...

    @abstractmethod
    def required_timeframes(self) -> list[str]:
        """이 전략이 필요로 하는 타임프레임 목록."""
        ...


# ── 전략 레지스트리 ──

_STRATEGIES: dict[str, type[BaseStrategy]] = {}

ACTIVE_STRATEGY = "topdown_mtf"


def register_strategy(cls: type[BaseStrategy]) -> type[BaseStrategy]:
    """전략 클래스 데코레이터. 등록과 동시에 클래스 반환."""
    _STRATEGIES[cls.name] = cls
    return cls


def get_strategy(name: str | None = None) -> BaseStrategy:
    """전략 인스턴스 반환. name 미지정 시 ACTIVE_STRATEGY 사용."""
    key = name or ACTIVE_STRATEGY
    if key not in _STRATEGIES:
        raise KeyError(f"등록되지 않은 전략: {key!r} (등록: {list(_STRATEGIES)})")
    return _STRATEGIES[key]()


def list_strategies() -> dict[str, str]:
    """등록된 전략 {name: description} 반환."""
    return {name: cls.description for name, cls in _STRATEGIES.items()}
