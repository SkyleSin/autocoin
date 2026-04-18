"""거래소 REST 등 외부 API 호출 횟수 제한 (워커 간 공유)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class AsyncSlidingWindowRateLimiter:
    """최근 `window_seconds` 구간 안에서 `max_calls`번을 넘지 않도록 `acquire`에서 대기한다."""

    def __init__(self, max_calls: int, *, window_seconds: float = 1.0) -> None:
        if max_calls <= 0:
            msg = "max_calls는 1 이상이어야 합니다."
            raise ValueError(msg)
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._throttle_events = 0

    async def acquire(self) -> None:
        while True:
            sleep_for: float = 0.0
            async with self._lock:
                now = time.monotonic()
                cutoff = now - self.window_seconds
                while self._times and self._times[0] <= cutoff:
                    self._times.popleft()
                if len(self._times) < self.max_calls:
                    self._times.append(now)
                    return
                sleep_for = self._times[0] + self.window_seconds - now
            wait = max(sleep_for, 0.0)
            if wait > 0.001:
                logger.debug(
                    "rate_limit: sleep %.3fs (cap %s/%ss)",
                    wait,
                    self.max_calls,
                    self.window_seconds,
                )
            if wait >= 0.02:
                self._throttle_events += 1
                logger.info(
                    "rate_limit: throttle #%s wait=%.3fs (max %s public API calls / %ss)",
                    self._throttle_events,
                    wait,
                    self.max_calls,
                    self.window_seconds,
                )
            await asyncio.sleep(wait)
