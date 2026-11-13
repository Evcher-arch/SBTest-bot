"""Правила рекомендаций: условия, получатель, ключ дедупликации, подписки, объединение.

Без PostgreSQL: выборки подменяются функцией select, хранилище сигналов — словарём в памяти.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from services.recommender import clients, db, engine
from services.recommender.rules import (
    RULE_IDS,
    Candidate,
    evaluate,
    group_by_recipient,
    load_rules,
    rule_by_id,
)

TODAY = date(2026, 11, 13)


def _invoice(number: str, client_id: str, overdue_days: int = 0, due: str = "2026-11-08",
             amount: float = 100000.0) -> dict:
    return {"number": number, "client_id": client_id, "client_name": f"Клиент {client_id}",
            "due_date": due, "amount": amount, "overdue_days": overdue_days,
            "url": f"http://localhost:8040/ui/invoices/{number}"}


class FakeSystems:
    """Ответы сервисных выборок; запоминает вызовы."""

    def __init__(self) -> None:
        self.invoices: dict[str, list[dict]] = {"overdue": [], "due_soon": []}
        self.managers = {"C001": "ivanov", "C006": "petrova"}
        self.events: list[dict] = []
        self.tickets: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    async def select(self, name: str, params: dict) -> list[dict]:
        self.calls.append((name, params))
        if name == "billing_select_invoices":
            return list(self.invoices[params["status"]])
        if name == "crm_select_client_managers":
            return [{"client_id": c, "name": c, "manager": {"id": self.managers[c]}}
                    for c in params["client_ids"] if c in self.managers]
        if name == "calendar_select_events":
            return [e for e in self.events if e["user"] in params["users"]]
        if name == "sd_select_stale_tickets":
            return list(self.tickets)
        raise AssertionError(name)


def _rules(*ids: str):
    return [rule_by_id(i) for i in ids]


def test_rules_yaml_has_four_rules_with_interval_within_sc007():
    rules = load_rules()
    assert {r.id for r in rules} == set(RULE_IDS)
    assert all(1 <= r.interval_seconds <= 120 for r in rules)


# --- условия и получатели ------------------------------------------------------


async def test_invoice_overdue_goes_to_client_manager():
    sys = FakeSystems()
    sys.invoices["overdue"] = [_invoice("СЧ-1", "C001", 5), _invoice("СЧ-2", "C006", 3),
                               _invoice("СЧ-3", "C001", 0)]
    out = await evaluate(_rules("invoice_overdue"), TODAY, sys.select)
    by_number = {c.object_id: c for c in out}
    assert set(by_number) == {"СЧ-1", "СЧ-2"}  # 0 дней просрочки — ниже порога
    assert by_number["СЧ-1"].recipient_id == "ivanov"
    assert by_number["СЧ-2"].recipient_id == "petrova"
    text = by_number["СЧ-1"].text
    assert "СЧ-1" in text and "Клиент C001" in text and "5 дн" in text and "100 000,00 ₽" in text
    assert by_number["СЧ-1"].url.endswith("/ui/invoices/СЧ-1")


async def test_invoice_without_manager_has_no_recipient():
    sys = FakeSystems()
    sys.invoices["overdue"] = [_invoice("СЧ-9", "C999", 4)]
    assert await evaluate(_rules("invoice_overdue"), TODAY, sys.select) == []


async def test_invoice_due_soon_asks_for_window_of_three_days():
    sys = FakeSystems()
    sys.invoices["due_soon"] = [_invoice("СЧ-5", "C001", 0, due="2026-11-15")]
    out = await evaluate(_rules("invoice_due_soon"), TODAY, sys.select)
    assert [c.object_id for c in out] == ["СЧ-5"]
    params = next(p for n, p in sys.calls if n == "billing_select_invoices")
    assert params == {"status": "due_soon", "due_before": (TODAY + timedelta(days=3)).isoformat()}


async def test_meeting_tomorrow_only_with_manager_or_client():
    sys = FakeSystems()
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    sys.events = [
        # Иванов и его руководитель Кузнецова
        {"id": "EV-1", "user": "ivanov", "subject": "Разбор воронки", "start": f"{tomorrow}T10:00:00+03:00",
         "organizer": {"id": "ivanov"}, "attendees": [{"employee_id": "kuznetsova"}]},
        # Петрова с клиентом
        {"id": "EV-2", "user": "petrova", "subject": "Переговоры", "start": f"{tomorrow}T14:00:00+03:00",
         "organizer": {"id": "petrova"}, "attendees": [{"name": "Орлов", "client_id": "C006"}]},
        # Иванов с коллегой — не руководитель и не клиент
        {"id": "EV-3", "user": "ivanov", "subject": "Обмен клиентами", "start": f"{tomorrow}T16:00:00+03:00",
         "organizer": {"id": "ivanov"}, "attendees": [{"employee_id": "petrova"}]},
    ]
    out = await evaluate(_rules("meeting_tomorrow"), TODAY, sys.select)
    assert {(c.object_id, c.recipient_id) for c in out} == {("EV-1", "ivanov"), ("EV-2", "petrova")}
    ev1 = next(c for c in out if c.object_id == "EV-1")
    assert "Разбор воронки" in ev1.text and "руководителем" in ev1.text
    params = next(p for n, p in sys.calls if n == "calendar_select_events")
    assert params["date"] == tomorrow


async def test_ticket_stale_goes_to_assignee():
    sys = FakeSystems()
    sys.tickets = [
        {"number": "SD-1", "title": "Монитор", "assignee": {"id": "sidorov"}, "status": "in_progress",
         "due_at": "2026-11-20T10:00:00+03:00", "updated_at": "2026-11-07T10:00:00+03:00",
         "idle_days": 6, "reason": "idle", "url": "http://localhost:8040/ui/tickets/SD-1"},
        {"number": "SD-2", "title": "Без исполнителя", "assignee": None, "status": "new",
         "due_at": None, "updated_at": "2026-11-01T10:00:00+03:00", "idle_days": 12, "reason": "idle"},
    ]
    out = await evaluate(_rules("ticket_stale"), TODAY, sys.select)
    assert [(c.object_id, c.recipient_id) for c in out] == [("SD-1", "sidorov")]
    assert "без движения 6 дн." in out[0].text
    params = next(p for n, p in sys.calls if n == "sd_select_stale_tickets")
    assert params["idle_days"] == 3


# --- ключ дедупликации и состояние ------------------------------------------------


async def test_dedup_key_and_new_state_gives_new_key():
    sys = FakeSystems()
    sys.invoices["overdue"] = [_invoice("СЧ-1", "C001", 5, due="2026-11-08")]
    [first] = await evaluate(_rules("invoice_overdue"), TODAY, sys.select)
    [again] = await evaluate(_rules("invoice_overdue"), TODAY, sys.select)
    key = lambda c: (c.rule, c.object_id, c.recipient_id, c.state_key)  # noqa: E731
    assert key(first) == key(again)
    assert set(first.as_row()) >= {"rule", "object_id", "recipient_id", "state_key"}

    sys.invoices["overdue"] = [_invoice("СЧ-1", "C001", 2, due="2026-11-11")]  # срок перенесли
    [moved] = await evaluate(_rules("invoice_overdue"), TODAY, sys.select)
    assert key(moved) != key(first)


async def test_disabled_subscription_drops_only_that_rule_for_that_employee():
    sys = FakeSystems()
    sys.invoices["overdue"] = [_invoice("СЧ-1", "C001", 5), _invoice("СЧ-2", "C006", 3)]
    sys.invoices["due_soon"] = [_invoice("СЧ-5", "C001", 0, due="2026-11-15")]
    out = await evaluate(_rules("invoice_overdue", "invoice_due_soon"), TODAY, sys.select,
                         disabled={"ivanov": {"invoice_due_soon"}})
    assert {(c.rule, c.recipient_id) for c in out} == {
        ("invoice_overdue", "ivanov"), ("invoice_overdue", "petrova")}


async def test_failed_rule_does_not_stop_others():
    sys = FakeSystems()
    sys.invoices["overdue"] = [_invoice("СЧ-1", "C001", 5)]

    async def select(name: str, params: dict) -> list[dict]:
        if name == "sd_select_stale_tickets":
            raise RuntimeError("down")
        return await sys.select(name, params)

    out = await evaluate(_rules("ticket_stale", "invoice_overdue"), TODAY, select)
    assert [c.object_id for c in out] == ["СЧ-1"]


def test_group_by_recipient():
    def cand(rule: str, obj: str, who: str) -> Candidate:
        return Candidate(rule=rule, object_id=obj, object_label=obj, recipient_id=who,
                         state_key="k", text=obj, url=None)

    groups = group_by_recipient([cand("invoice_overdue", "A", "ivanov"), cand("ticket_stale", "B", "sidorov"),
                                 cand("meeting_tomorrow", "C", "ivanov")])
    assert {k: [c.object_id for c in v] for k, v in groups.items()} == {"ivanov": ["A", "C"],
                                                                        "sidorov": ["B"]}


# --- проход правил целиком (engine.run) без базы -----------------------------------


class MemorySignals:
    def __init__(self) -> None:
        self.rows: dict[tuple, dict] = {}
        self.disabled: dict[str, set[str]] = {}
        self._ids = 0

    async def disabled_rules(self):
        return self.disabled

    async def insert_new(self, signals):
        created = []
        for s in signals:
            key = (s["rule"], s["object_id"], s["recipient_id"], s["state_key"])
            if key in self.rows:
                continue
            self._ids += 1
            row = db.StoredSignal(self._ids, s["rule"], s["object_id"], s["recipient_id"], s["text"], s["url"])
            self.rows[key] = {"row": row, "sent": False}
            created.append(row)
        return created

    async def unsent(self):
        return [v["row"] for v in self.rows.values() if not v["sent"]]

    async def mark_sent(self, ids):
        for v in self.rows.values():
            if v["row"].id in ids:
                v["sent"] = True


@pytest.fixture
def run_env(monkeypatch):
    sys = FakeSystems()
    store = MemorySignals()
    sent: list[tuple[str, str, list[str]]] = []
    formulated: list[tuple[str, list[dict]]] = []

    async def notify(employee_id, text, urls):
        sent.append((employee_id, text, urls))
        return 1

    async def formulate(name, signals):
        formulated.append((name, signals))
        return None  # сбой модели → шаблон

    for name in ("disabled_rules", "insert_new", "unsent", "mark_sent"):
        monkeypatch.setattr(db, name, getattr(store, name))
    monkeypatch.setattr(clients, "select", sys.select)
    monkeypatch.setattr(clients, "notify", notify)
    monkeypatch.setattr(clients, "formulate", formulate)
    return sys, store, sent, formulated


async def test_engine_merges_signals_of_recipient_and_dedups(run_env):
    sys, store, sent, formulated = run_env
    sys.invoices["overdue"] = [_invoice("СЧ-1", "C001", 5), _invoice("СЧ-2", "C006", 3)]
    sys.invoices["due_soon"] = [_invoice("СЧ-5", "C001", 0, due="2026-11-15")]
    rules = _rules("invoice_overdue", "invoice_due_soon")

    result = await engine.run(rules)
    assert result == {"signals_created": 3, "notifications_sent": 2}
    by_who = {who: text for who, text, _ in sent}
    assert set(by_who) == {"ivanov", "petrova"}
    # два сигнала Иванова — одним сообщением; при сбое модели — текст по шаблону
    assert "СЧ-1" in by_who["ivanov"] and "СЧ-5" in by_who["ivanov"]
    assert by_who["ivanov"].startswith("Иван, важное на сегодня:")
    assert len(next(s for n, s in formulated if n.startswith("Иванов"))) == 2

    sent.clear()
    assert await engine.run(rules) == {"signals_created": 0, "notifications_sent": 0}
    assert sent == []

    # новое состояние объекта — новый сигнал
    sys.invoices["overdue"][0] = _invoice("СЧ-1", "C001", 1, due="2026-11-12")
    assert (await engine.run(rules))["signals_created"] == 1
    assert [who for who, _, _ in sent] == ["ivanov"]


async def test_engine_respects_subscription_and_retries_failed_delivery(run_env, monkeypatch):
    sys, store, sent, _ = run_env
    sys.invoices["overdue"] = [_invoice("СЧ-1", "C001", 5)]
    sys.invoices["due_soon"] = [_invoice("СЧ-5", "C001", 0, due="2026-11-15")]
    store.disabled = {"ivanov": {"invoice_due_soon"}}
    rules = _rules("invoice_overdue", "invoice_due_soon")

    async def broken_notify(*_a, **_k):
        return None

    monkeypatch.setattr(clients, "notify", broken_notify)
    result = await engine.run(rules)
    assert result == {"signals_created": 1, "notifications_sent": 0}

    async def notify(employee_id, text, urls):
        sent.append((employee_id, text, urls))
        return 1

    monkeypatch.setattr(clients, "notify", notify)
    result = await engine.run(rules)
    assert result == {"signals_created": 0, "notifications_sent": 1}
    [(who, text, urls)] = sent
    assert who == "ivanov" and "СЧ-1" in text and "СЧ-5" not in text
    assert urls == ["http://localhost:8040/ui/invoices/СЧ-1"]
