"""Загрузка seed/ в хранилище заглушки с пересчётом смещений дат от текущего дня."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml
from common.settings import get_settings

from .models import Store, iso

_OFFSET = re.compile(r"^\s*([+-]?\d+)d(?:\s+(\d{1,2}):(\d{2}))?\s*$")


def _match(value: Any) -> re.Match[str]:
    m = _OFFSET.match(str(value))
    if not m:
        raise ValueError(f"неверное смещение даты: {value!r} (ожидается вид -5d или +1d 10:00)")
    return m


def offset_date(value: Any, today: date | None = None) -> date:
    """'-5d' → дата на 5 дней раньше сегодняшней."""
    today = today or get_settings().today()
    return today + timedelta(days=int(_match(value).group(1)))


def offset_datetime(value: Any, now: datetime | None = None) -> datetime:
    """'+1d 10:00' → завтра в 10:00 по часовому поясу стенда; '-6d' → 6 дней назад, текущее время."""
    now = now or get_settings().now()
    m = _match(value)
    day = now.date() + timedelta(days=int(m.group(1)))
    if m.group(2) is not None:
        return datetime.combine(day, time(int(m.group(2)), int(m.group(3))), tzinfo=now.tzinfo)
    return now.replace(microsecond=0) + timedelta(days=int(m.group(1)))


def _yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def parse_page(text: str) -> tuple[dict, str]:
    """Метаданные в начале файла между строками '---' и тело Markdown."""
    if not text.startswith("---"):
        raise ValueError("у страницы нет блока метаданных")
    _, meta, body = text.split("---", 2)
    return yaml.safe_load(meta), body.strip() + "\n"


def load_seed(store: Store, seed_dir: Path | None = None) -> dict[str, int]:
    seed_dir = seed_dir or get_settings().seed_path
    now = get_settings().now()
    today = now.date()
    store.clear()

    employees = _yaml(seed_dir / "employees.yaml")
    for e in employees:
        store.insert(
            "employee",
            {"id": e["id"], "name": e["name"], "position": e.get("position", ""),
             "groups": e.get("groups") or [], "manager_id": None},
        )
    for e in employees:
        if e.get("manager_id"):
            store.update("employee", "id", e["id"], {"manager_id": e["manager_id"]})

    for c in _yaml(seed_dir / "clients.yaml"):
        store.insert("client", {"client_id": c["client_id"], "name": c["name"],
                                "inn": str(c["inn"]), "manager_id": c["manager_id"]})

    for inv in _yaml(seed_dir / "invoices.yaml"):
        store.insert("invoice", {
            "number": inv["number"],
            "client_id": inv["client_id"],
            "date": offset_date(inv["date"], today).isoformat(),
            "due_date": offset_date(inv["due"], today).isoformat(),
            "amount": float(inv["amount"]),
            "status": inv["status"],
            "payments": [
                {"date": offset_date(p["date"], today).isoformat(), "amount": float(p["amount"])}
                for p in inv.get("payments") or []
            ],
        })

    for t in _yaml(seed_dir / "ticket_types.yaml"):
        store.insert("ticket_type", {"type_id": t["type_id"], "name": t["name"],
                                     "fields": t.get("fields") or [], "sla_hours": t["sla_hours"]})

    for t in _yaml(seed_dir / "tickets.yaml"):
        fields = dict(t.get("fields") or {})
        for key, value in list(fields.items()):
            if isinstance(value, str) and _OFFSET.match(value):
                fields[key] = offset_date(value, today).isoformat()
        store.insert("ticket", {
            "number": t["number"],
            "type_id": t["type_id"],
            "title": t["title"],
            "description": t.get("description", ""),
            "fields": fields,
            "status": t["status"],
            "author_id": t["author_id"],
            "assignee_id": t.get("assignee_id"),
            "created_at": iso(offset_datetime(t["created"], now)),
            "updated_at": iso(offset_datetime(t["updated"], now)),
            "due_at": iso(offset_datetime(t["due"], now)) if t.get("due") else None,
            "history": [
                {"at": iso(offset_datetime(h["at"], now)), "actor": h["actor"], "action": h["action"]}
                for h in t.get("history") or []
            ],
            "idempotency_key": None,
        })

    for ev in _yaml(seed_dir / "events.yaml"):
        store.insert("calendar_event", {
            "id": ev["id"],
            "subject": ev["subject"],
            "start": iso(offset_datetime(ev["start"], now)),
            "end": iso(offset_datetime(ev["end"], now)),
            "organizer_id": ev["organizer_id"],
            "attendees": ev.get("attendees") or [],
        })

    for path in sorted((seed_dir / "pages").glob("*.md")):
        meta, body = parse_page(path.read_text(encoding="utf-8"))
        updated = iso(offset_datetime(meta.get("updated", "0d"), now))
        store.insert("page", {
            "page_id": meta["page_id"],
            "space": meta["space"],
            "title": meta["title"],
            "body": body,
            "version": 1,
            "updated_at": updated,
            "allowed_groups": meta.get("allowed_groups") or [],
            "acl_updated_at": updated,
        })

    return store.counts()
