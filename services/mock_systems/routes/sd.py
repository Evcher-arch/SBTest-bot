"""Service-Desk: обращения, типы, создание с ключом идемпотентности, сервисная выборка."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from common.errors import ApiError
from common.schemas import SELECT_LIMIT, list_response
from common.settings import get_settings
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth import Caller, can_see_ticket, current_employee, outage_guard, require_recs_service
from ..models import OPEN_TICKET_STATUSES, Store, get_store, iso, parse_dt
from .common import ticket_view

router = APIRouter(prefix="/sd", tags=["Service-Desk"], dependencies=[Depends(outage_guard("sd"))])

STATUSES = ("new", "in_progress", "waiting", "resolved", "closed")


@router.get("/ticket-types", summary="Типы обращений и их поля")
async def ticket_types(
    _: Caller = Depends(current_employee), store: Store = Depends(get_store)
) -> dict:
    items = [
        {"type_id": t["type_id"], "name": t["name"], "sla_hours": t["sla_hours"], "fields": t["fields"]}
        for t in store.ticket_types()
    ]
    return list_response(items)


@router.get("/tickets/select", summary="Обращения без движения или с истекающим сроком (recs-service)")
async def select_stale(
    idle_days: int = Query(..., ge=1),
    due_within: float | None = Query(None, ge=0),
    _: object = Depends(require_recs_service),
    store: Store = Depends(get_store),
) -> dict:
    now = get_settings().now()
    items = []
    for t in store.tickets():
        if t["status"] not in OPEN_TICKET_STATUSES or not t["assignee_id"]:
            continue
        idle = now - parse_dt(t["updated_at"]) >= timedelta(days=idle_days)
        due_soon = (
            due_within is not None
            and t["due_at"] is not None
            and parse_dt(t["due_at"]) <= now + timedelta(hours=due_within)
        )
        if not (idle or due_soon):
            continue
        view = ticket_view(store, t)
        items.append({
            "number": view["number"], "title": view["title"], "assignee": view["assignee"],
            "status": view["status"], "due_at": view["due_at"], "updated_at": view["updated_at"],
            "idle_days": (now - parse_dt(t["updated_at"])).days,
            "reason": "idle" if idle else "due_soon", "url": view["url"],
        })
    return list_response(items, SELECT_LIMIT)


@router.get("/tickets", summary="Обращения сотрудника")
async def list_tickets(
    role: str = Query(...),
    status: str | None = Query(None),
    employee: str | None = Query(None),
    caller: Caller = Depends(current_employee),
    store: Store = Depends(get_store),
) -> dict:
    if role not in ("author", "assignee"):
        raise ApiError("invalid_input", "role: допустимы author или assignee.")
    if status is not None and status not in STATUSES:
        raise ApiError("invalid_input", f"status: допустимы {', '.join(STATUSES)}.")
    target = caller.id
    if employee and employee != caller.id:
        if store.employee(employee) is None:
            raise ApiError("not_found", f"Сотрудник {employee} не найден.")
        if employee not in store.subordinates(caller.id):
            raise ApiError("forbidden", "Обращения этого сотрудника вам недоступны.")
        target = employee
    column = "author_id" if role == "author" else "assignee_id"
    items = [
        ticket_view(store, t)
        for t in store.tickets()
        if t[column] == target and (status is None or t["status"] == status)
    ]
    return list_response(items)


@router.get("/tickets/{number}", summary="Карточка обращения с историей")
async def get_ticket(
    number: str, caller: Caller = Depends(current_employee), store: Store = Depends(get_store)
) -> dict:
    ticket = store.ticket(number)
    if ticket is None:
        raise ApiError("not_found", f"Обращение {number} не найдено.")
    if not can_see_ticket(store, caller.id, ticket):
        raise ApiError("forbidden", f"Нет доступа к обращению {number}.")
    return ticket_view(store, ticket, full=True)


class CreateTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type_id: str
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)


def _validate_fields(ttype: dict, values: dict[str, Any]) -> dict[str, Any]:
    spec = {f["name"]: f for f in ttype["fields"]}
    unknown = set(values) - set(spec)
    if unknown:
        raise ApiError("invalid_input", f"Лишние поля для типа «{ttype['name']}»: {', '.join(sorted(unknown))}.")
    clean: dict[str, Any] = {}
    for name, field in spec.items():
        value = values.get(name)
        if value in (None, ""):
            if field.get("required"):
                raise ApiError("invalid_input", f"Не заполнено обязательное поле «{field['label']}».")
            continue
        if field["type"] == "enum" and value not in (field.get("options") or []):
            raise ApiError(
                "invalid_input",
                f"Поле «{field['label']}»: допустимы {', '.join(field.get('options') or [])}.",
            )
        if field["type"] == "date":
            try:
                date.fromisoformat(str(value))
            except ValueError as exc:
                raise ApiError("invalid_input", f"Поле «{field['label']}»: ожидается дата.") from exc
        clean[name] = value
    return clean


@router.post("/tickets", summary="Создать обращение (заголовок Idempotency-Key обязателен)")
async def create_ticket(
    body: CreateTicket,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    caller: Caller = Depends(current_employee),
    store: Store = Depends(get_store),
) -> JSONResponse:
    if not idempotency_key:
        raise ApiError("invalid_input", "Не передан заголовок Idempotency-Key.")
    existing = store.ticket_by_key(idempotency_key)
    if existing is not None:
        if existing["author_id"] != caller.id:
            raise ApiError("forbidden", "Ключ идемпотентности принадлежит другому сотруднику.")
        return JSONResponse(_created(store, existing), status_code=200)
    ttype = store.ticket_type(body.type_id)
    if ttype is None:
        raise ApiError("invalid_input", f"Неизвестный тип обращения: {body.type_id}.")
    fields = _validate_fields(ttype, body.fields)
    now = get_settings().now().replace(microsecond=0)
    number = store.next_ticket_number()
    store.insert("ticket", {
        "number": number,
        "type_id": ttype["type_id"],
        "title": body.title.strip(),
        "description": body.description.strip(),
        "fields": fields,
        "status": "new",
        "author_id": caller.id,
        "assignee_id": None,
        "created_at": iso(now),
        "updated_at": iso(now),
        "due_at": iso(now + timedelta(hours=float(ttype["sla_hours"]))),
        "history": [{"at": iso(now), "actor": caller.id, "action": "Обращение создано"}],
        "idempotency_key": idempotency_key,
    })
    return JSONResponse(_created(store, store.ticket(number)), status_code=201)


def _created(store: Store, ticket: dict) -> dict:
    view = ticket_view(store, ticket)
    return {"number": view["number"], "status": view["status"], "url": view["url"],
            "type": view["type"], "title": view["title"], "due_at": view["due_at"]}
