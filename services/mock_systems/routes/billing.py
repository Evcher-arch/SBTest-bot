"""Биллинг: счета клиента, счёт с оплатами, сервисная выборка."""

from __future__ import annotations

from datetime import date, timedelta

from common.errors import ApiError
from common.schemas import SELECT_LIMIT, list_response
from common.settings import get_settings
from fastapi import APIRouter, Depends, Query

from ..auth import (
    Caller,
    can_see_client,
    client_or_raise,
    current_employee,
    outage_guard,
    require_recs_service,
)
from ..models import Store, get_store, overdue_days
from .common import invoice_view

router = APIRouter(
    prefix="/billing", tags=["Биллинг"], dependencies=[Depends(outage_guard("billing"))]
)

STATUSES = ("issued", "partially_paid", "paid", "overdue", "cancelled")


def _parse_date(value: str | None, field: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ApiError("invalid_input", f"Поле {field}: ожидается дата ГГГГ-ММ-ДД.") from exc


@router.get("/invoices/select", summary="Выборка счетов по всем клиентам (recs-service)")
async def select_invoices(
    status: str = Query(...),
    due_before: str | None = Query(None),
    _: object = Depends(require_recs_service),
    store: Store = Depends(get_store),
) -> dict:
    if status not in ("overdue", "due_soon"):
        raise ApiError("invalid_input", "status: допустимы overdue или due_soon.")
    today = get_settings().today()
    limit = _parse_date(due_before, "due_before")
    items = []
    for inv in store.invoices():
        if inv["status"] in ("paid", "cancelled"):
            continue
        due = date.fromisoformat(inv["due_date"])
        days = overdue_days(inv, today)
        if status == "overdue":
            if days <= 0 or (limit and due >= limit):
                continue
        else:
            horizon = limit or today + timedelta(days=3)
            if not (today <= due <= horizon):
                continue
        view = invoice_view(store, inv)
        items.append({k: view[k] for k in ("number", "client_id", "client_name", "due_date",
                                           "amount", "paid_amount", "overdue_days", "url")})
    return list_response(items, SELECT_LIMIT)


@router.get("/invoices", summary="Счета клиента")
async def list_invoices(
    client_id: str = Query(...),
    status: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    caller: Caller = Depends(current_employee),
    store: Store = Depends(get_store),
) -> dict:
    if status is not None and status not in STATUSES:
        raise ApiError("invalid_input", f"status: допустимы {', '.join(STATUSES)}.")
    start, end = _parse_date(from_, "from"), _parse_date(to, "to")
    client_or_raise(store, caller, client_id)
    items = []
    for inv in store.invoices(client_id):
        view = invoice_view(store, inv)
        if status is not None and view["status"] != status:
            continue
        inv_date = date.fromisoformat(inv["date"])
        if (start and inv_date < start) or (end and inv_date > end):
            continue
        items.append(view)
    return list_response(items)


@router.get("/invoices/{number}", summary="Счёт с оплатами")
async def get_invoice(
    number: str, caller: Caller = Depends(current_employee), store: Store = Depends(get_store)
) -> dict:
    inv = store.invoice(number)
    if inv is None:
        raise ApiError("not_found", f"Счёт {number} не найден.")
    client = store.client(inv["client_id"])
    if client is None or not can_see_client(store, caller.id, client):
        raise ApiError("forbidden", f"Нет доступа к счёту {number}.")
    return invoice_view(store, inv, full=True)
