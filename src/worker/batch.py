"""`Running` 상태로 잡힌 Job들을 API 호출 단위로 묶어 `WorkBatch`로 만든다."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from models.job import Job


@dataclass(frozen=True)
class WorkBatch:
    """큐에 넣는 작업 단위. 워커는 `job_ids`로 DB에서 다시 로드해 처리한다."""

    job_ids: tuple[uuid.UUID, ...]
    job_type: str
    merged: dict[str, Any]


def _content_market(content: dict[str, Any] | None) -> str | None:
    if not content:
        return None
    m = content.get("market")
    if m is not None:
        return str(m).strip() or None
    ms = content.get("markets")
    if ms is not None:
        parts = [p.strip() for p in str(ms).split(",") if p.strip()]
        return parts[0] if parts else None
    return None


def _batch_bithumb_ticker(group: list[Job]) -> list[WorkBatch]:
    batches: list[WorkBatch] = []
    missing_market = [j for j in group if not _content_market(j.content)]
    for j in missing_market:
        batches.append(
            WorkBatch(job_ids=(j.id,), job_type="bithumb_ticker", merged=dict(j.content or {})),
        )
    ok = [j for j in group if _content_market(j.content)]
    if not ok:
        return batches
    pairs: list[tuple[uuid.UUID, str]] = []
    for j in ok:
        m = _content_market(j.content)
        if m:
            pairs.append((j.id, m))
    if not pairs:
        return batches
    order_preserving: list[str] = []
    seen: set[str] = set()
    for _, m in pairs:
        if m not in seen:
            seen.add(m)
            order_preserving.append(m)
    markets_param = ",".join(order_preserving)
    ids = tuple(uid for uid, _ in pairs)
    batches.append(
        WorkBatch(
            job_ids=ids,
            job_type="bithumb_ticker",
            merged={"markets": markets_param, "_pairs": pairs},
        ),
    )
    return batches


def build_work_batches(jobs: list[Job]) -> list[WorkBatch]:
    if not jobs:
        return []
    by_type: dict[str, list[Job]] = {}
    for j in jobs:
        by_type.setdefault(j.type, []).append(j)
    out: list[WorkBatch] = []
    for jtype, group in by_type.items():
        if jtype == "bithumb_ticker":
            out.extend(_batch_bithumb_ticker(group))
        elif jtype == "bithumb_markets":
            out.append(
                WorkBatch(
                    job_ids=tuple(j.id for j in group),
                    job_type=jtype,
                    merged={},
                ),
            )
        else:
            for j in group:
                out.append(
                    WorkBatch(
                        job_ids=(j.id,),
                        job_type=j.type,
                        merged=dict(j.content or {}),
                    ),
                )
    return out
