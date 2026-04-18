"""Todo CRUD 라우터."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.todo import Todo
from utils.deps import get_db

router = APIRouter(prefix="/api/todos", tags=["todos"])


class TodoCreate(BaseModel):
    content: str
    tag: str = "general"


class TodoUpdate(BaseModel):
    done: bool | None = None
    content: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: TodoCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = Todo(content=body.content, tag=body.tag)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


@router.get("")
async def list_todos(
    db: Annotated[AsyncSession, Depends(get_db)],
    done: bool | None = Query(None),
) -> list[dict[str, Any]]:
    q = select(Todo)
    if done is not None:
        q = q.where(Todo.done == done)
    q = q.order_by(Todo.created_at.desc())
    rows = (await db.execute(q)).scalars().all()
    return [_to_dict(r) for r in rows]


@router.patch("/{todo_id}")
async def patch(
    todo_id: uuid.UUID,
    body: TodoUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = await db.get(Todo, todo_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return _to_dict(row)


@router.get("/count")
async def count(
    db: Annotated[AsyncSession, Depends(get_db)],
    done: bool | None = Query(None),
) -> dict[str, int]:
    q = select(func.count()).select_from(Todo)
    if done is not None:
        q = q.where(Todo.done == done)
    cnt = (await db.execute(q)).scalar() or 0
    return {"count": cnt}


def _to_dict(r: Todo) -> dict[str, Any]:
    return {
        "id": str(r.id), "content": r.content, "tag": r.tag,
        "done": r.done, "created_at": r.created_at.isoformat(),
    }
