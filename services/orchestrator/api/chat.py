"""POST /chat, POST /sessions/{session_id}/reset, POST /admin/reset."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .. import drafts
from ..agent.loop import run_agent
from ..db import get_history

router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1, max_length=100)
    employee_id: str = Field(..., min_length=1, max_length=100)
    text: str = Field(..., min_length=1, max_length=4000)


@router.post("/chat")
async def chat(body: ChatRequest) -> dict:
    result = await run_agent(body.session_id, body.employee_id, body.text.strip())
    return {"trace_id": result.trace_id, "text": result.text, "sources": result.sources,
            "draft": result.draft}


@router.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str) -> dict:
    deleted = await get_history().delete_session(session_id)
    cancelled = await drafts.cancel_session(session_id)
    return {"messages_deleted": deleted, "drafts_cancelled": cancelled}


@router.post("/admin/reset")
async def admin_reset() -> dict:
    messages = await get_history().clear()
    removed = await drafts.clear_all()
    return {"messages_deleted": messages, "drafts_deleted": removed}
