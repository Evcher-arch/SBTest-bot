"""Общие схемы ответов и маршрут /health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

LIST_LIMIT = 20
SELECT_LIMIT = 200


def list_response(items: list[Any], limit: int = LIST_LIMIT) -> dict:
    """Ответ-список {items, has_more}: не больше limit записей."""
    return {"items": items[:limit], "has_more": len(items) > limit}


def health_router(service: str) -> APIRouter:
    router = APIRouter()

    @router.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "service": service}

    return router
