"""Доставка уведомлений сервиса рекомендаций во все чаты, где выбрана роль сотрудника."""

from __future__ import annotations

import logging

from aiogram import Bot
from common.redis_client import get_redis

from .render import split_message
from .roles import chats_key

log = logging.getLogger(__name__)


async def deliver(bot: Bot | None, employee_id: str, text: str, urls: list[str] | None = None) -> int:
    chat_ids = await get_redis().smembers(chats_key(employee_id))
    if not chat_ids or bot is None:
        return 0
    body = "🔔 " + text
    missing = [u for u in urls or [] if u not in text]
    if missing:
        body += "\n" + "\n".join(missing)
    delivered = 0
    for chat_id in chat_ids:
        try:
            for part in split_message(body):
                await bot.send_message(int(chat_id), part, disable_web_page_preview=True)
            delivered += 1
        except Exception:  # noqa: BLE001 — недоступный чат не мешает остальным
            log.warning("notify to chat failed")
    return delivered


async def reset_state() -> int:
    """Удалить роли, списки чатов и токены (сброс стенда)."""
    redis = get_redis()
    roles = 0
    for pattern in ("role:*", "chats:*", "token:*"):
        keys = [k async for k in redis.scan_iter(match=pattern, count=500)]
        if keys:
            removed = await redis.delete(*keys)
            if pattern == "role:*":
                roles = removed
    return roles
