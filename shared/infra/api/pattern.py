"""PatternConfig / PatternEvaluation / PatternAccuracy API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_post

# ── PatternConfig ──

async def get_pattern_configs() -> list[dict]:
    return await api_get("/api/pattern-configs")


# ── PatternEvaluation ──

async def create_pattern_evaluation(data: dict) -> dict:
    return await api_post("/api/pattern-evaluations", json=data)


async def get_pattern_eval_stats() -> list[dict]:
    return await api_get("/api/pattern-evaluations/stats")


# ── PatternAccuracy ──

async def upsert_pattern_accuracy(data: dict) -> dict:
    return await api_post("/api/pattern-accuracy/upsert", json=data)


async def get_accuracy_summary(
    include_inactive: bool = False,
) -> list[dict]:
    return await api_get(
        "/api/pattern-accuracy/summary",
        params={"include_inactive": include_inactive},
    )
