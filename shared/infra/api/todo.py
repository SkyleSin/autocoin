"""Todo API 래퍼."""

from __future__ import annotations

from infra.api_client import api_get, api_patch, api_post


async def create_todo(content: str) -> dict:
    return await api_post("/api/todos", json={"content": content})


async def get_todos(done: bool = False) -> list[dict]:
    return await api_get("/api/todos", params={"done": done})


async def complete_todo(todo_id: int) -> dict:
    return await api_patch(f"/api/todos/{todo_id}", json={"done": True})


async def get_todo_count(done: bool = False) -> int:
    r = await api_get("/api/todos/count", params={"done": done})
    return r["count"]
