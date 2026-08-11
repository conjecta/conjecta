from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from math_agent.web.security import require_auth_user
from math_agent.web.user_memory_store import MemoryStatus, UserMemory, UserMemoryStore

log = logging.getLogger("math_agent.web.user_memory_routes")
router = APIRouter(prefix="/api/me/memories", tags=["user-memory"])


def user_memory_store_for_user(user_id: str) -> UserMemoryStore:
    base = Path(os.getenv("CONJECTA_USER_MEMORY_DIR") or "logs/users").resolve()
    return UserMemoryStore(user_id=user_id, root=base)


def _store_for_request(request: Request) -> UserMemoryStore:
    user = require_auth_user(request)
    try:
        return user_memory_store_for_user(user.user_id)
    except Exception as exc:
        log.exception("User memory store is unavailable for %s", user.user_id)
        raise HTTPException(status_code=503, detail="User memory store is unavailable.") from exc


def _public_memory(memory: UserMemory) -> dict[str, object]:
    return {
        "id": memory.id,
        "kind": memory.kind.value,
        "content": memory.content,
        "why": memory.why,
        "weight": memory.weight,
        "status": memory.status.value,
        "scope": str(memory.scope),
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


class UserMemoryPatch(BaseModel):
    status: Literal["candidate", "active", "snoozed"] | None = None
    content: str | None = Field(default=None, max_length=300)
    why: str | None = Field(default=None, max_length=200)


@router.get("")
async def list_user_memories(
    request: Request,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    store = _store_for_request(request)
    parsed_status: MemoryStatus | None = None
    if status:
        if status not in {"candidate", "active", "snoozed"}:
            raise HTTPException(status_code=400, detail="Unsupported memory status.")
        parsed_status = MemoryStatus(status)
    bounded_limit = max(0, min(int(limit), 500))
    memories = store.list(
        status=parsed_status,
        limit=bounded_limit,
        offset=max(0, int(offset)),
    )
    profile = store.get_profile()
    return {
        "ok": True,
        "memories": [_public_memory(memory) for memory in memories],
        "profile": (
            {
                "summary": profile.summary,
                "version": profile.version,
                "generated_at": profile.generated_at,
            }
            if profile is not None and profile.summary
            else None
        ),
    }


@router.patch("/{memory_id}")
async def update_user_memory(
    memory_id: str,
    payload: UserMemoryPatch,
    request: Request,
):
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No memory changes were provided.")
    if "content" in fields:
        fields["content"] = str(fields["content"]).strip()
        if not fields["content"]:
            raise HTTPException(status_code=400, detail="Memory content is required.")
    if "why" in fields:
        fields["why"] = str(fields["why"]).strip()

    store = _store_for_request(request)
    try:
        updated = store.update(memory_id, fields)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid memory update.") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"ok": True, "memory": _public_memory(updated)}


@router.delete("/profile")
async def clear_user_profile(request: Request):
    store = _store_for_request(request)
    store.clear_profile()
    return {"ok": True}


@router.delete("/{memory_id}")
async def delete_user_memory(memory_id: str, request: Request):
    store = _store_for_request(request)
    if not store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"ok": True}
