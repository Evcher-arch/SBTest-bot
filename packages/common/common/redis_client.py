"""Общий клиент Redis; тесты подменяют его через set_redis."""

from __future__ import annotations

from redis.asyncio import Redis

from .settings import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def set_redis(client: Redis | None) -> None:
    global _redis
    _redis = client
