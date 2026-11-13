"""Общие помощники маршрутов заглушки: ссылки на карточки и представления объектов."""

from __future__ import annotations

from urllib.parse import quote

from common.settings import get_settings

from ..models import Store, display_status, overdue_days


def card_url(kind: str, key: str) -> str:
    return f"{get_settings().mock_public_url.rstrip('/')}/ui/{kind}/{quote(key, safe='')}"


def client_view(store: Store, client: dict) -> dict:
    return {
        "client_id": client["client_id"],
        "name": client["name"],
        "inn": client["inn"],
        "manager": store.employee_ref(client["manager_id"]),
        "url": card_url("clients", client["client_id"]),
    }


def invoice_view(store: Store, invoice: dict, *, full: bool = False) -> dict:
    client = store.client(invoice["client_id"])
    paid = round(sum(p["amount"] for p in invoice["payments"]), 2)
    view = {
        "number": invoice["number"],
        "client_id": invoice["client_id"],
        "client_name": client["name"] if client else invoice["client_id"],
        "date": invoice["date"],
        "due_date": invoice["due_date"],
        "amount": invoice["amount"],
        "paid_amount": paid,
        "status": display_status(invoice),
        "overdue_days": overdue_days(invoice),
        "url": card_url("invoices", invoice["number"]),
    }
    if full:
        view["payments"] = invoice["payments"]
        view["debt"] = round(invoice["amount"] - paid, 2) if invoice["status"] != "cancelled" else 0
    return view


def ticket_view(store: Store, ticket: dict, *, full: bool = False) -> dict:
    ttype = store.ticket_type(ticket["type_id"])
    view = {
        "number": ticket["number"],
        "type": ttype["name"] if ttype else ticket["type_id"],
        "type_id": ticket["type_id"],
        "title": ticket["title"],
        "status": ticket["status"],
        "author": store.employee_ref(ticket["author_id"]),
        "assignee": store.employee_ref(ticket["assignee_id"]),
        "created_at": ticket["created_at"],
        "updated_at": ticket["updated_at"],
        "due_at": ticket["due_at"],
        "url": card_url("tickets", ticket["number"]),
    }
    if full:
        labels = {f["name"]: f["label"] for f in (ttype["fields"] if ttype else [])}
        view["description"] = ticket["description"]
        view["fields"] = [
            {"name": k, "label": labels.get(k, k), "value": v} for k, v in ticket["fields"].items()
        ]
        view["history"] = ticket["history"]
    return view


def attendee_view(store: Store, attendee: dict) -> dict:
    if attendee.get("employee_id"):
        return store.employee_ref(attendee["employee_id"]) | {"employee_id": attendee["employee_id"]}
    return {"name": attendee.get("name", ""), "client_id": attendee.get("client_id")}


def event_view(store: Store, event: dict) -> dict:
    return {
        "id": event["id"],
        "subject": event["subject"],
        "start": event["start"],
        "end": event["end"],
        "organizer": store.employee_ref(event["organizer_id"]),
        "attendees": [attendee_view(store, a) for a in event["attendees"]],
    }
