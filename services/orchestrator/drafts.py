"""Черновики обращений в Redis.

Состояния: pending → confirming → confirmed; ошибка возвращает в pending;
cancelled (кнопка «Отменить», смена роли, сброс); expired (истёк срок — ключ удалён по TTL).
action_id служит ключом идемпотентности для sd_create_ticket.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

from common.redis_client import get_redis
from common.settings import get_settings

RESULT_TTL_S = 24 * 3600
LOCK_TTL_S = 30


def _draft_key(action_id: str) -> str:
    return f"draft:{action_id}"


def _result_key(action_id: str) -> str:
    return f"draft_result:{action_id}"


def _lock_key(action_id: str) -> str:
    return f"draft_lock:{action_id}"


def _session_key(session_id: str) -> str:
    return f"drafts:{session_id}"


async def create(*, session_id: str, employee_id: str, type_id: str, type_name: str, title: str,
                 description: str, fields: dict[str, Any], field_labels: dict[str, str],
                 trace_id: str | None) -> dict:
    s = get_settings()
    action_id = str(uuid.uuid4())
    expires = s.now() + timedelta(seconds=s.draft_ttl_s)
    draft = {
        "action_id": action_id,
        "session_id": session_id,
        "employee_id": employee_id,
        "type_id": type_id,
        "type_name": type_name,
        "title": title,
        "description": description,
        "fields": fields,
        "field_labels": field_labels,
        "status": "pending",
        "expires_at": expires.isoformat(timespec="seconds"),
        "trace_id": trace_id,
    }
    redis = get_redis()
    await redis.set(_draft_key(action_id), json.dumps(draft, ensure_ascii=False), ex=s.draft_ttl_s)
    await redis.sadd(_session_key(session_id), action_id)
    await redis.expire(_session_key(session_id), s.draft_ttl_s)
    return draft


def public_view(draft: dict) -> dict:
    """Черновик для ответа /chat (contracts/internal-api.md)."""
    labels = draft.get("field_labels") or {}
    return {
        "action_id": draft["action_id"],
        "type_name": draft["type_name"],
        "title": draft["title"],
        "description": draft["description"],
        "fields": [{"label": labels.get(k, k), "value": v} for k, v in draft["fields"].items()],
        "expires_at": draft["expires_at"],
    }


async def get(action_id: str) -> dict | None:
    raw = await get_redis().get(_draft_key(action_id))
    return json.loads(raw) if raw else None


async def _set_status(action_id: str, status: str) -> dict | None:
    draft = await get(action_id)
    if draft is None:
        return None
    draft["status"] = status
    await get_redis().set(_draft_key(action_id), json.dumps(draft, ensure_ascii=False), keepttl=True)
    return draft


async def get_result(action_id: str) -> dict | None:
    raw = await get_redis().get(_result_key(action_id))
    return json.loads(raw) if raw else None


async def try_begin_confirm(action_id: str) -> str:
    """Атомарный переход pending → confirming.

    Возвращает 'ok' (можно создавать), 'busy' (подтверждение уже идёт),
    'missing' (истёк или не существует) либо текущее состояние черновика.
    """
    redis = get_redis()
    if not await redis.set(_lock_key(action_id), "1", nx=True, ex=LOCK_TTL_S):
        return "busy"
    draft = await get(action_id)
    if draft is None:
        await redis.delete(_lock_key(action_id))
        return "missing"
    if draft["status"] != "pending":
        await redis.delete(_lock_key(action_id))
        return draft["status"]
    await _set_status(action_id, "confirming")
    return "ok"


async def finish_confirm(action_id: str, result: dict) -> None:
    redis = get_redis()
    await redis.set(_result_key(action_id), json.dumps(result, ensure_ascii=False), ex=RESULT_TTL_S)
    await _set_status(action_id, "confirmed")
    await redis.delete(_lock_key(action_id))


async def fail_confirm(action_id: str) -> None:
    await _set_status(action_id, "pending")
    await get_redis().delete(_lock_key(action_id))


async def wait_result(action_id: str, timeout_s: float = 10.0) -> dict | None:
    """Параллельное подтверждение ждёт результата первого."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        result = await get_result(action_id)
        if result is not None:
            return result
        if not await get_redis().exists(_lock_key(action_id)):
            return await get_result(action_id)
        await asyncio.sleep(0.1)
    return None


async def cancel(action_id: str) -> dict | None:
    draft = await get(action_id)
    if draft is None or draft["status"] != "pending":
        return draft
    return await _set_status(action_id, "cancelled")


async def cancel_session(session_id: str) -> int:
    redis = get_redis()
    cancelled = 0
    for action_id in await redis.smembers(_session_key(session_id)):
        draft = await get(action_id)
        if draft and draft["status"] == "pending":
            await _set_status(action_id, "cancelled")
            cancelled += 1
    await redis.delete(_session_key(session_id))
    return cancelled


async def clear_all() -> int:
    redis = get_redis()
    deleted = 0
    for pattern in ("draft:*", "draft_result:*", "draft_lock:*", "drafts:*"):
        keys = [k async for k in redis.scan_iter(match=pattern, count=500)]
        if not keys:
            continue
        removed = await redis.delete(*keys)
        if pattern in ("draft:*", "draft_result:*"):
            deleted += removed
    return deleted
