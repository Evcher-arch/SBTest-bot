"""Токен сессии из Redis. Берётся в момент вызова инструмента и никуда не записывается."""

from __future__ import annotations

from common.redis_client import get_redis


async def session_token(session_id: str) -> str | None:
    return await get_redis().get(f"token:{session_id}")
