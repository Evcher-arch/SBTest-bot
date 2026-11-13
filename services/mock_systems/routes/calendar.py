"""Календарь: встречи владельца токена и сервисная выборка на дату."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from common.errors import ApiError
from common.schemas import SELECT_LIMIT, list_response
from common.settings import get_settings
from fastapi import APIRouter, Depends, Query

from ..auth import Caller, current_employee, outage_guard, require_recs_service
from ..models import Store, get_store, parse_dt
from .common import event_view

router = APIRouter(
    prefix="/calendar", tags=["Календарь"], dependencies=[Depends(outage_guard("calendar"))]
)

MAX_RANGE = timedelta(days=31)


def _participants(event: dict) -> set[str]:
    people = {a["employee_id"] for a in event["attendees"] if a.get("employee_id")}
    return people | {event["organizer_id"]}


def _parse(value: str, field: str) -> datetime:
    try:
        return parse_dt(value)
    except ValueError as exc:
        raise ApiError("invalid_input", f"Поле {field}: ожидается дата-время ISO 8601.") from exc


@router.get("/events/select", summary="Встречи сотрудников на дату (recs-service)")
async def select_events(
    date_: str = Query(..., alias="date"),
    users: list[str] = Query(default=[]),
    _: object = Depends(require_recs_service),
    store: Store = Depends(get_store),
) -> dict:
    try:
        day = date.fromisoformat(date_)
    except ValueError as exc:
        raise ApiError("invalid_input", "date: ожидается дата ГГГГ-ММ-ДД.") from exc
    wanted = {u for raw in users for u in raw.split(",") if u}
    tz = get_settings().tz
    items = []
    for event in store.events():
        if parse_dt(event["start"]).astimezone(tz).date() != day:
            continue
        for user in sorted(_participants(event) & wanted if wanted else _participants(event)):
            view = event_view(store, event)
            items.append({"user": user, **{k: view[k] for k in
                          ("id", "subject", "start", "end", "organizer", "attendees")}})
    return list_response(items, SELECT_LIMIT)


@router.get("/events", summary="Встречи владельца токена за период")
async def list_events(
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    caller: Caller = Depends(current_employee),
    store: Store = Depends(get_store),
) -> dict:
    start, end = _parse(from_, "from"), _parse(to, "to")
    if end <= start:
        raise ApiError("invalid_input", "Конец интервала должен быть позже начала.")
    if end - start > MAX_RANGE:
        raise ApiError("invalid_input", "Интервал — не больше 31 дня.")
    items = [
        event_view(store, ev)
        for ev in store.events()
        if caller.id in _participants(ev)
        and parse_dt(ev["start"]) < end
        and parse_dt(ev["end"]) > start
    ]
    return list_response(items)
