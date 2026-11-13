"""Хранилище заглушки систем: SQLite в памяти (data-model.md, раздел 1)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from common.settings import get_settings

SCHEMA = """
CREATE TABLE employee (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    groups TEXT NOT NULL DEFAULT '[]',
    manager_id TEXT REFERENCES employee(id)
);
CREATE TABLE client (
    client_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    inn TEXT NOT NULL UNIQUE CHECK (length(inn) IN (10, 12) AND inn NOT GLOB '*[^0-9]*'),
    manager_id TEXT NOT NULL REFERENCES employee(id)
);
CREATE TABLE invoice (
    number TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES client(client_id),
    date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL CHECK (status IN ('issued', 'partially_paid', 'paid', 'cancelled')),
    payments TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE ticket_type (
    type_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    fields TEXT NOT NULL DEFAULT '[]',
    sla_hours REAL NOT NULL
);
CREATE TABLE ticket (
    number TEXT PRIMARY KEY,
    type_id TEXT NOT NULL REFERENCES ticket_type(type_id),
    title TEXT NOT NULL CHECK (length(title) <= 200),
    description TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('new', 'in_progress', 'waiting', 'resolved', 'closed')),
    author_id TEXT NOT NULL REFERENCES employee(id),
    assignee_id TEXT REFERENCES employee(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    due_at TEXT,
    history TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT UNIQUE
);
CREATE TABLE calendar_event (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    start TEXT NOT NULL,
    "end" TEXT NOT NULL,
    organizer_id TEXT NOT NULL REFERENCES employee(id),
    attendees TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE page (
    page_id TEXT PRIMARY KEY,
    space TEXT NOT NULL CHECK (space IN ('HR', 'PROC', 'MGMT')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    allowed_groups TEXT NOT NULL DEFAULT '[]',
    acl_updated_at TEXT NOT NULL
);
"""

JSON_COLUMNS = {"groups", "payments", "fields", "history", "attendees", "allowed_groups"}
SYSTEMS = ("crm", "billing", "sd", "calendar", "confluence")
OPEN_TICKET_STATUSES = ("new", "in_progress", "waiting")


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for key in JSON_COLUMNS & out.keys():
        out[key] = json.loads(out[key])
    return out


def _dump(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_settings().tz)
    return dt


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def overdue_days(invoice: dict, today: date | None = None) -> int:
    """Дней после due_date, если статус не paid и не cancelled, иначе 0."""
    if invoice["status"] in ("paid", "cancelled"):
        return 0
    today = today or get_settings().today()
    delta = (today - date.fromisoformat(invoice["due_date"])).days
    return max(delta, 0)


def display_status(invoice: dict, today: date | None = None) -> str:
    return "overdue" if overdue_days(invoice, today) > 0 else invoice["status"]


class Store:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.outages: set[str] = set()

    # --- служебное ---
    def clear(self) -> None:
        with self.conn:
            for table in ("ticket", "ticket_type", "invoice", "client", "calendar_event", "page"):
                self.conn.execute(f"DELETE FROM {table}")
            self.conn.execute("UPDATE employee SET manager_id = NULL")
            self.conn.execute("DELETE FROM employee")
        self.outages.clear()

    def insert(self, table: str, row: dict) -> None:
        cols = list(row.keys())
        quoted = ", ".join(f'"{c}"' for c in cols)
        marks = ", ".join("?" for _ in cols)
        with self.conn:
            self.conn.execute(
                f"INSERT INTO {table} ({quoted}) VALUES ({marks})", [_dump(row[c]) for c in cols]
            )

    def update(self, table: str, key: str, key_value: str, changes: dict) -> None:
        if not changes:
            return
        sets = ", ".join(f'"{c}" = ?' for c in changes)
        with self.conn:
            self.conn.execute(
                f"UPDATE {table} SET {sets} WHERE {key} = ?",
                [_dump(v) for v in changes.values()] + [key_value],
            )

    def _one(self, sql: str, *args: Any) -> dict | None:
        return _row(self.conn.execute(sql, args).fetchone())

    def _all(self, sql: str, *args: Any) -> list[dict]:
        return [_row(r) for r in self.conn.execute(sql, args).fetchall()]  # type: ignore[misc]

    def counts(self) -> dict[str, int]:
        tables = ("employee", "client", "invoice", "ticket", "ticket_type", "calendar_event", "page")
        return {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}

    # --- сотрудники ---
    def employee(self, employee_id: str) -> dict | None:
        return self._one("SELECT * FROM employee WHERE id = ?", employee_id)

    def employees(self) -> list[dict]:
        return self._all("SELECT * FROM employee ORDER BY id")

    def subordinates(self, employee_id: str) -> list[str]:
        rows = self.conn.execute("SELECT id FROM employee WHERE manager_id = ?", (employee_id,))
        return [r[0] for r in rows]

    def manager_of(self, employee_id: str) -> str | None:
        emp = self.employee(employee_id)
        return emp["manager_id"] if emp else None

    def employee_ref(self, employee_id: str | None) -> dict | None:
        if not employee_id:
            return None
        emp = self.employee(employee_id)
        return {"id": employee_id, "name": emp["name"] if emp else employee_id}

    # --- CRM ---
    def client(self, client_id: str) -> dict | None:
        return self._one("SELECT * FROM client WHERE client_id = ?", client_id)

    def clients(self) -> list[dict]:
        return self._all("SELECT * FROM client ORDER BY name")

    # --- биллинг ---
    def invoice(self, number: str) -> dict | None:
        return self._one("SELECT * FROM invoice WHERE number = ?", number)

    def invoices(self, client_id: str | None = None) -> list[dict]:
        if client_id:
            return self._all(
                "SELECT * FROM invoice WHERE client_id = ? ORDER BY date DESC, number DESC", client_id
            )
        return self._all("SELECT * FROM invoice ORDER BY date DESC, number DESC")

    # --- Service-Desk ---
    def ticket_type(self, type_id: str) -> dict | None:
        return self._one("SELECT * FROM ticket_type WHERE type_id = ?", type_id)

    def ticket_types(self) -> list[dict]:
        return self._all("SELECT * FROM ticket_type ORDER BY rowid")

    def ticket(self, number: str) -> dict | None:
        return self._one("SELECT * FROM ticket WHERE number = ?", number)

    def ticket_by_key(self, key: str) -> dict | None:
        return self._one("SELECT * FROM ticket WHERE idempotency_key = ?", key)

    def tickets(self) -> list[dict]:
        return self._all("SELECT * FROM ticket ORDER BY created_at DESC, number DESC")

    def next_ticket_number(self) -> str:
        numbers = [r[0] for r in self.conn.execute("SELECT number FROM ticket")]
        top = max((int(n.split("-")[1]) for n in numbers if n.startswith("SD-")), default=1000)
        return f"SD-{top + 1}"

    # --- календарь ---
    def event(self, event_id: str) -> dict | None:
        return self._one('SELECT * FROM calendar_event WHERE id = ?', event_id)

    def events(self) -> list[dict]:
        return self._all('SELECT * FROM calendar_event ORDER BY start')

    def next_event_id(self) -> str:
        ids = [r[0] for r in self.conn.execute("SELECT id FROM calendar_event")]
        top = max((int(i.split("-")[1]) for i in ids if i.startswith("EV-")), default=0)
        return f"EV-{top + 1:03d}"

    # --- портал документации ---
    def page(self, page_id: str) -> dict | None:
        return self._one("SELECT * FROM page WHERE page_id = ?", page_id)

    def pages(self) -> list[dict]:
        return self._all("SELECT * FROM page ORDER BY page_id")


store = Store()


def get_store() -> Store:
    return store
