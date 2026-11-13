"""POST /feedback: оценка сотрудника к трассе. Сбой Langfuse не возвращается как ошибка."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..tracing import get_tracer

router = APIRouter()


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str = Field(..., min_length=1, max_length=64)
    value: Literal[1, -1]


@router.post("/feedback")
async def feedback(body: FeedbackRequest) -> dict:
    tracer = get_tracer()
    tracer.score(body.trace_id, float(body.value))
    tracer.flush()
    return {"ok": True}
