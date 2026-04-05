#!/usr/bin/env python3
"""1분 동안 매초 `bithumb_markets` 잡을 N번 생성해 15/s API 리미터를 스트레스한다.

- 잡 생성(POST /jobs)은 DB만 건드리므로 가볍고, 실제 빗썸 호출은 워커가 하며 `BITHUMB_MAX_REQUESTS_PER_SEC`에 막힌다.
- 서버가 떠 있어야 함. 기본: http://127.0.0.1:8000

환경 변수:
  AUTOCOIN_TEST_BASE     API 베이스 URL
  AUTOCOIN_TEST_PER_SEC  초당 생성 잡 수 (기본 20)
  AUTOCOIN_TEST_SECONDS  실행 초 (기본 60)
"""

from __future__ import annotations

import os
import sys
import time

import httpx


def main() -> None:
    base = os.environ.get("AUTOCOIN_TEST_BASE", "http://127.0.0.1:8000").rstrip("/")
    per_sec = int(os.environ.get("AUTOCOIN_TEST_PER_SEC", "30"))
    seconds = int(os.environ.get("AUTOCOIN_TEST_SECONDS", "60"))

    total = 0
    t0 = time.perf_counter()
    with httpx.Client(base_url=base, timeout=120.0) as client:
        for sec in range(seconds):
            lap = time.perf_counter()
            for _ in range(per_sec):
                r = client.post("/jobs", json={"type": "bithumb_markets"})
                r.raise_for_status()
                total += 1
            rest = max(0.0, 1.0 - (time.perf_counter() - lap))
            time.sleep(rest)
            if (sec + 1) % 10 == 0:
                print(f"t={sec + 1}s jobs_created={total}", flush=True)

    wall = time.perf_counter() - t0
    print(f"done jobs_created={total} wall_sec={wall:.1f}", flush=True)
    if wall > seconds + 2:
        print(
            "(참고) 생성 루프가 길어지면 네트워크/서버 부하일 수 있음.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
