"""Хранилище сервиса рекомендаций: отправленные сигналы и подписки."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.settings import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DDL = [
    """
    CREATE TABLE IF NOT EXISTS signal (
        id bigserial PRIMARY KEY,
        rule text NOT NULL CHECK (rule IN ('invoice_overdue', 'invoice_due_soon',
                                           'meeting_tomorrow', 'ticket_stale')),
        object_id text NOT NULL,
        recipient_id text NOT NULL,
        state_key text NOT NULL,
        text text NOT NULL,
        url text,
        created_at timestamptz NOT NULL DEFAULT now(),
        sent_at timestamptz,
        UNIQUE (rule, object_id, recipient_id, state_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscription (
        employee_id text NOT NULL,
        rule text NOT NULL,
        enabled boolean NOT NULL DEFAULT true,
        PRIMARY KEY (employee_id, rule)
    )
    """,
]

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


async def init_db() -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(434343)"))
        for statement in DDL:
            await conn.execute(text(statement))


@dataclass
class StoredSignal:
    id: int
    rule: str
    object_id: str
    recipient_id: str
    text: str
    url: str | None


async def disabled_rules() -> dict[str, set[str]]:
    """employee_id → множество отключённых правил."""
    async with get_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT employee_id, rule FROM subscription WHERE enabled = false"))).all()
    result: dict[str, set[str]] = {}
    for r in rows:
        result.setdefault(r.employee_id, set()).add(r.rule)
    return result


async def set_subscription(employee_id: str, rule: str, enabled: bool) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text("""
            INSERT INTO subscription (employee_id, rule, enabled) VALUES (:e, :r, :en)
            ON CONFLICT (employee_id, rule) DO UPDATE SET enabled = EXCLUDED.enabled
        """), {"e": employee_id, "r": rule, "en": enabled})


async def subscriptions(employee_id: str, rules: list[str]) -> list[dict]:
    async with get_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT rule, enabled FROM subscription WHERE employee_id = :e"), {"e": employee_id})).all()
    state = {r.rule: r.enabled for r in rows}
    return [{"rule": rule, "enabled": state.get(rule, True)} for rule in rules]


async def insert_new(signals: list[dict[str, Any]]) -> list[StoredSignal]:
    """Вставка с отбрасыванием дублей по (rule, object_id, recipient_id, state_key)."""
    created: list[StoredSignal] = []
    async with get_engine().begin() as conn:
        for s in signals:
            row = (await conn.execute(text("""
                INSERT INTO signal (rule, object_id, recipient_id, state_key, text, url)
                VALUES (:rule, :object_id, :recipient_id, :state_key, :text, :url)
                ON CONFLICT (rule, object_id, recipient_id, state_key) DO NOTHING
                RETURNING id
            """), {k: s[k] for k in ("rule", "object_id", "recipient_id", "state_key", "text", "url")})).first()
            if row is not None:
                created.append(StoredSignal(row.id, s["rule"], s["object_id"], s["recipient_id"],
                                            s["text"], s["url"]))
    return created


async def unsent() -> list[StoredSignal]:
    """Сигналы, доставка которых не удалась ранее (за последние сутки)."""
    async with get_engine().connect() as conn:
        rows = (await conn.execute(text("""
            SELECT id, rule, object_id, recipient_id, text, url FROM signal
            WHERE sent_at IS NULL AND created_at > now() - interval '1 day'
            ORDER BY id
        """))).all()
    return [StoredSignal(r.id, r.rule, r.object_id, r.recipient_id, r.text, r.url) for r in rows]


async def mark_sent(ids: list[int]) -> None:
    if not ids:
        return
    async with get_engine().begin() as conn:
        await conn.execute(text("UPDATE signal SET sent_at = now() WHERE id = ANY(CAST(:ids AS bigint[]))"),
                           {"ids": ids})


async def count_signals() -> int:
    async with get_engine().connect() as conn:
        return (await conn.execute(text("SELECT count(*) FROM signal"))).scalar_one()


async def reset() -> dict:
    async with get_engine().begin() as conn:
        signals = (await conn.execute(text("DELETE FROM signal"))).rowcount
        subs = (await conn.execute(text("DELETE FROM subscription"))).rowcount
    return {"signals_deleted": signals or 0, "subscriptions_deleted": subs or 0}
