"""Пульт ведущего: сброс стенда, изменение данных, имитация отказов. Без авторизации (Swagger UI)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Literal

from common.errors import ApiError
from common.http import make_client
from common.settings import get_settings
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..models import SYSTEMS, Store, get_store, iso, parse_dt
from ..seed_loader import load_seed

router = APIRouter(prefix="/admin", tags=["Пульт ведущего"])
log = logging.getLogger(__name__)


async def _call(name: str, url: str, timeout: float) -> tuple[str, Any]:
    try:
        async with make_client(timeout=timeout) as client:
            resp = await client.post(url)
        if resp.status_code >= 400:
            return name, {"ok": False, "error": f"HTTP {resp.status_code}"}
        return name, {"ok": True, "result": resp.json()}
    except Exception as exc:  # noqa: BLE001 — недоступность сервиса не прерывает цепочку
        log.warning("reset chain: %s unavailable: %s", name, exc)
        return name, {"ok": False, "error": type(exc).__name__}


@router.post("/reset", summary="Вернуть стенд в исходное состояние")
async def reset(store: Store = Depends(get_store)) -> dict:
    counts = load_seed(store)
    s = get_settings()
    # Сначала рекомендации, оркестратор и бот (быстро), затем переиндексация (дольше)
    chain = await asyncio.gather(
        _call("recommender", f"{s.recommender_url}/admin/reset", 10),
        _call("orchestrator", f"{s.orchestrator_url}/admin/reset", 10),
        _call("bot", f"{s.bot_url}/admin/reset", 10),
    )
    reindex = await _call("search", f"{s.search_url}/admin/reindex", 50)
    services = dict(chain) | dict([reindex])
    return {
        "mock_systems": {"ok": True, "result": counts},
        **services,
        "reset": ["mock_systems"] + [k for k, v in services.items() if v["ok"]],
        "failed": [k for k, v in services.items() if not v["ok"]],
    }


class Payment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    amount: float = Field(..., gt=0)


class InvoicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_date: date | None = None
    status: Literal["issued", "partially_paid", "paid", "cancelled"] | None = None
    add_payment: Payment | None = None


@router.patch("/invoices/{number}", summary="Изменить счёт: срок, статус, добавить оплату")
async def patch_invoice(number: str, body: InvoicePatch, store: Store = Depends(get_store)) -> dict:
    inv = store.invoice(number)
    if inv is None:
        raise ApiError("not_found", f"Счёт {number} не найден.")
    changes: dict[str, Any] = {}
    if body.due_date:
        changes["due_date"] = body.due_date.isoformat()
    if body.status:
        changes["status"] = body.status
    if body.add_payment:
        changes["payments"] = inv["payments"] + [
            {"date": body.add_payment.date.isoformat(), "amount": body.add_payment.amount}
        ]
    store.update("invoice", "number", number, changes)
    return store.invoice(number)


class TicketPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["new", "in_progress", "waiting", "resolved", "closed"] | None = None
    assignee_id: str | None = None
    updated_at: str | None = Field(None, description="ISO 8601; по умолчанию — сейчас")
    due_at: str | None = None


@router.patch("/tickets/{number}", summary="Изменить обращение")
async def patch_ticket(number: str, body: TicketPatch, store: Store = Depends(get_store)) -> dict:
    ticket = store.ticket(number)
    if ticket is None:
        raise ApiError("not_found", f"Обращение {number} не найдено.")
    if body.assignee_id and store.employee(body.assignee_id) is None:
        raise ApiError("invalid_input", f"Сотрудник {body.assignee_id} не найден.")
    try:
        updated = parse_dt(body.updated_at) if body.updated_at else get_settings().now()
        due = iso(parse_dt(body.due_at)) if body.due_at else None
    except ValueError as exc:
        raise ApiError("invalid_input", "Дата-время ожидается в формате ISO 8601.") from exc
    changes: dict[str, Any] = {"updated_at": iso(updated.replace(microsecond=0))}
    actions = []
    if body.status:
        changes["status"] = body.status
        actions.append(f"Статус изменён на {body.status}")
    if body.assignee_id:
        changes["assignee_id"] = body.assignee_id
        actions.append(f"Назначено на {body.assignee_id}")
    if due:
        changes["due_at"] = due
        actions.append("Изменён срок")
    if actions:
        changes["history"] = ticket["history"] + [
            {"at": changes["updated_at"], "actor": "admin", "action": "; ".join(actions)}
        ]
    store.update("ticket", "number", number, changes)
    return store.ticket(number)


class PagePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str | None = Field(None, min_length=1)
    allowed_groups: list[str] | None = None


@router.patch("/pages/{page_id}", summary="Изменить текст или права страницы")
async def patch_page(page_id: str, body: PagePatch, store: Store = Depends(get_store)) -> dict:
    page = store.page(page_id)
    if page is None:
        raise ApiError("not_found", f"Страница {page_id} не найдена.")
    now = iso(get_settings().now().replace(microsecond=0))
    changes: dict[str, Any] = {}
    if body.body is not None and body.body != page["body"]:
        changes |= {"body": body.body, "version": page["version"] + 1, "updated_at": now}
    if body.allowed_groups is not None and sorted(body.allowed_groups) != sorted(page["allowed_groups"]):
        changes |= {"allowed_groups": body.allowed_groups, "acl_updated_at": now}
    store.update("page", "page_id", page_id, changes)
    return store.page(page_id)


class Attendee(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str | None = None
    name: str | None = None
    client_id: str | None = None


class NewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(..., min_length=1)
    start: str
    end: str
    organizer_id: str
    attendees: list[Attendee] = Field(default_factory=list)


@router.post("/events", summary="Добавить встречу")
async def add_event(body: NewEvent, store: Store = Depends(get_store)) -> dict:
    if store.employee(body.organizer_id) is None:
        raise ApiError("invalid_input", f"Сотрудник {body.organizer_id} не найден.")
    try:
        start, end = parse_dt(body.start), parse_dt(body.end)
    except ValueError as exc:
        raise ApiError("invalid_input", "start и end — дата-время ISO 8601.") from exc
    if end <= start:
        raise ApiError("invalid_input", "Конец встречи должен быть позже начала.")
    event_id = store.next_event_id()
    store.insert("calendar_event", {
        "id": event_id, "subject": body.subject, "start": iso(start), "end": iso(end),
        "organizer_id": body.organizer_id,
        "attendees": [a.model_dump(exclude_none=True) for a in body.attendees],
    })
    return store.event(event_id)


class Outage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system: Literal["crm", "billing", "sd", "calendar", "confluence"]
    enabled: bool


@router.post("/outage", summary="Имитировать недоступность системы")
async def outage(body: Outage, store: Store = Depends(get_store)) -> dict:
    if body.enabled:
        store.outages.add(body.system)
    else:
        store.outages.discard(body.system)
    return {"outages": sorted(store.outages)}


@router.get("/state", summary="Сводка: число объектов и включённые отказы")
async def state(store: Store = Depends(get_store)) -> dict:
    return {"counts": store.counts(), "outages": sorted(store.outages), "systems": list(SYSTEMS)}
