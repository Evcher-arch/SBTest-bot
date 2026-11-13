"""Проход правил: выборка → условие → получатель → подписка → дедупликация →
одно сообщение на получателя → /formulate (при сбое — шаблон) → /notify → sent_at."""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from common import directory
from common.settings import get_settings

from . import clients, db
from .rules import Rule, evaluate, group_by_recipient, load_rules

log = logging.getLogger(__name__)
_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    """Один проход правил за раз в пределах цикла событий (lock на каждый цикл)."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(id(loop))
    if lock is None:
        _locks.clear()
        lock = _locks[id(loop)] = asyncio.Lock()
    return lock


async def candidates_for(today: date, rules: list[Rule] | tuple[Rule, ...] | None = None):
    disabled = await db.disabled_rules()
    return await evaluate(rules or load_rules(), today, clients.select, disabled)


def template_message(name: str, signals: list) -> str:
    first = name.split()[1] if len(name.split()) > 1 else name
    lines = [f"{first}, важное на сегодня:"]
    for s in signals:
        lines.append(f"• {s.text}" + (f"\n  {s.url}" if s.url else ""))
    return "\n".join(lines)


async def run(rules: list[Rule] | tuple[Rule, ...] | None = None) -> dict:
    async with _lock():
        today = get_settings().today()
        candidates = await candidates_for(today, rules)
        by_key = {(c.rule, c.object_id, c.recipient_id): c for c in candidates}
        created = await db.insert_new([c.as_row() for c in candidates])
        created_ids = {s.id for s in created}
        pending = created + [s for s in await db.unsent() if s.id not in created_ids]
        sent = 0
        for recipient, signals in group_by_recipient(pending).items():
            emp = directory.get_employee(recipient)
            name = emp.name if emp else recipient
            payload = []
            for s in signals:
                cand = by_key.get((s.rule, s.object_id, s.recipient_id))
                payload.append({"rule": s.rule, "object": cand.object_label if cand else s.object_id,
                                "facts": cand.facts if cand else {}, "template_text": s.text,
                                "url": s.url})
            text = await clients.formulate(name, payload) or template_message(name, signals)
            delivered = await clients.notify(recipient, text, [s.url for s in signals if s.url])
            if delivered is None:
                log.warning("notify failed for %s, will retry", recipient)
                continue
            await db.mark_sent([s.id for s in signals])
            sent += 1
        return {"signals_created": len(created), "notifications_sent": sent}
