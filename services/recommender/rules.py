"""Загрузка правил из rules.yaml и вычисление сигналов кодом.

Шаги «выборка → условие → получатель» не пишут в базу; их используют и проход по расписанию
(engine.run), и сводка по запросу (GET /signals).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from common import directory
from common.settings import get_settings

log = logging.getLogger(__name__)

RULES_PATH = Path(__file__).with_name("rules.yaml")
RULE_IDS = ("invoice_overdue", "invoice_due_soon", "meeting_tomorrow", "ticket_stale")

Select = Callable[[str, dict], Awaitable[list[dict]]]


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    selection: str
    params: dict[str, Any]
    condition: dict[str, Any]
    recipient: str
    interval_seconds: int
    template: str


@dataclass
class Candidate:
    rule: str
    object_id: str
    object_label: str
    recipient_id: str
    state_key: str
    text: str
    url: str | None
    facts: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict:
        return {"rule": self.rule, "object_id": self.object_id, "recipient_id": self.recipient_id,
                "state_key": self.state_key, "text": self.text, "url": self.url}


@lru_cache(maxsize=1)
def load_rules(path: Path = RULES_PATH) -> tuple[Rule, ...]:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    rules = []
    for r in raw:
        if r["id"] not in RULE_IDS:
            raise ValueError(f"unknown rule id {r['id']}")
        interval = int(r["schedule"]["interval_seconds"])
        if not 1 <= interval <= 120:
            raise ValueError(f"{r['id']}: interval must be within 1..120 s (SC-007)")
        rules.append(Rule(id=r["id"], title=r["title"], selection=r["source"]["selection"],
                          params=dict(r["source"].get("params") or {}), condition=dict(r["condition"]),
                          recipient=r["recipient"], interval_seconds=interval,
                          template=" ".join(r["template"].split())))
    return tuple(rules)


def rule_by_id(rule_id: str) -> Rule:
    return next(r for r in load_rules() if r.id == rule_id)


def money(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def render(template: str, facts: dict[str, Any]) -> str:
    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return "—"

    return template.format_map(_Safe(facts))


# --- условия и получатели --------------------------------------------------


async def _invoices(rule: Rule, today: date, select: Select) -> list[Candidate]:
    params: dict[str, Any] = {"status": rule.params["status"]}
    if rule.params["status"] == "due_soon":
        params["due_before"] = (today + timedelta(days=int(rule.condition["days_before_due"]))).isoformat()
    items = await select(rule.selection, params)
    if rule.id == "invoice_overdue":
        items = [i for i in items if i["overdue_days"] >= int(rule.condition["min_overdue_days"])]
    if not items:
        return []
    managers = await select("crm_select_client_managers",
                            {"client_ids": sorted({i["client_id"] for i in items})[:100]})
    manager_by_client = {m["client_id"]: (m.get("manager") or {}).get("id") for m in managers}
    result = []
    for inv in items:
        recipient = manager_by_client.get(inv["client_id"])
        if not recipient:
            continue
        facts = {
            "number": inv["number"], "client_name": inv.get("client_name", inv["client_id"]),
            "amount": inv["amount"], "amount_text": money(inv["amount"] - inv.get("paid_amount", 0)),
            "due_date": inv["due_date"], "overdue_days": inv.get("overdue_days", 0),
        }
        result.append(Candidate(
            rule=rule.id, object_id=inv["number"], object_label=f"Счёт {inv['number']}",
            recipient_id=recipient, state_key=inv["due_date"],
            text=render(rule.template, facts), url=inv.get("url"), facts=facts,
        ))
    return result


async def _meetings(rule: Rule, today: date, select: Select) -> list[Candidate]:
    day = today + timedelta(days=int(rule.params.get("day_offset", 1)))
    users = [e.id for e in directory.all_employees()]
    items = await select(rule.selection, {"date": day.isoformat(), "users": users})
    wanted = set(rule.condition.get("with") or [])
    result = []
    for ev in items:
        user = ev["user"]
        manager = directory.manager_of(user)
        others = [a for a in ev.get("attendees", []) if a.get("employee_id") != user]
        organizer = (ev.get("organizer") or {}).get("id")
        employee_ids = {a.get("employee_id") for a in others if a.get("employee_id")}
        if organizer and organizer != user:
            employee_ids.add(organizer)
        with_whom = []
        if "manager" in wanted and manager and manager in employee_ids:
            emp = directory.get_employee(manager)
            with_whom.append(f"с руководителем {emp.name if emp else manager}")
        if "client" in wanted:
            for a in others:
                if a.get("client_id"):
                    with_whom.append(f"с клиентом: {a.get('name')}")
        if not with_whom:
            continue
        start = datetime.fromisoformat(ev["start"]).astimezone(get_settings().tz)
        facts = {"subject": ev["subject"], "time": start.strftime("%H:%M"), "date": day.isoformat(),
                 "with_whom": "; ".join(with_whom)}
        result.append(Candidate(
            rule=rule.id, object_id=ev["id"], object_label=f"Встреча «{ev['subject']}»",
            recipient_id=user, state_key=ev["start"], text=render(rule.template, facts),
            url=None, facts=facts,
        ))
    return result


async def _tickets(rule: Rule, today: date, select: Select) -> list[Candidate]:
    params = {"idle_days": int(rule.condition["idle_days"])}
    if rule.condition.get("due_within_hours") is not None:
        params["due_within"] = rule.condition["due_within_hours"]
    items = await select(rule.selection, params)
    result = []
    for t in items:
        assignee = (t.get("assignee") or {}).get("id")
        if not assignee:
            continue
        idle = t.get("reason") == "idle"
        due = datetime.fromisoformat(t["due_at"]).astimezone(get_settings().tz) if t.get("due_at") else None
        facts = {
            "number": t["number"], "title": t["title"], "idle_days": t.get("idle_days", 0),
            "reason_text": f"без движения {t.get('idle_days', 0)} дн." if idle else "истекает срок",
            "due_text": due.strftime("%Y-%m-%d %H:%M") if due else "не указан",
        }
        result.append(Candidate(
            rule=rule.id, object_id=t["number"], object_label=f"Обращение {t['number']}",
            recipient_id=assignee, state_key=f"{t.get('updated_at', '')}|{t.get('reason')}",
            text=render(rule.template, facts), url=t.get("url"), facts=facts,
        ))
    return result


EVALUATORS = {
    "invoice_overdue": _invoices,
    "invoice_due_soon": _invoices,
    "meeting_tomorrow": _meetings,
    "ticket_stale": _tickets,
}


async def evaluate(rules: list[Rule] | tuple[Rule, ...], today: date, select: Select,
                   disabled: dict[str, set[str]] | None = None) -> list[Candidate]:
    """Выборка → условие → получатель → отсев по подписке. Ничего не записывает."""
    disabled = disabled or {}
    out: list[Candidate] = []
    for rule in rules:
        try:
            candidates = await EVALUATORS[rule.id](rule, today, select)
        except Exception:  # noqa: BLE001 — сбой одного правила не останавливает остальные
            log.warning("rule %s failed", rule.id, exc_info=True)
            continue
        out.extend(c for c in candidates if rule.id not in disabled.get(c.recipient_id, set()))
    return out


def group_by_recipient(candidates: list[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for c in candidates:
        groups.setdefault(c.recipient_id, []).append(c)
    return groups
