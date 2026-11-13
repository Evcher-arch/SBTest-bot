"""CRM: поиск клиентов, карточка клиента, сервисная выборка ответственных."""

from __future__ import annotations

from common.errors import ApiError
from common.schemas import SELECT_LIMIT, list_response
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..auth import (
    Caller,
    client_or_raise,
    current_employee,
    outage_guard,
    require_recs_service,
    visible_managers,
)
from ..models import Store, get_store
from .common import client_view

router = APIRouter(prefix="/crm", tags=["CRM"], dependencies=[Depends(outage_guard("crm"))])


_QUOTES = str.maketrans({c: " " for c in "«»\"'„“”‘’"})


def _words(value: str) -> list[str]:
    return value.translate(_QUOTES).replace("ё", "е").replace("Ё", "Е").casefold().split()


def name_matches(query: str, name: str) -> bool:
    """Все слова запроса входят в название; кавычки, регистр и «ё» не учитываются.

    «ООО Ромашка», «ромашка» и «ООО «Ромашка»» находят ООО «Ромашка».
    """
    haystack = " ".join(_words(name))
    return all(word in haystack for word in _words(query))


@router.get("/clients", summary="Поиск клиентов (только свои и подчинённых)")
async def find_clients(
    name: str | None = Query(None),
    inn: str | None = Query(None),
    caller: Caller = Depends(current_employee),
    store: Store = Depends(get_store),
) -> dict:
    if (name is None) == (inn is None):
        raise ApiError("invalid_input", "Укажите ровно одно из полей: name или inn.")
    if name is not None and len(name.strip()) < 2:
        raise ApiError("invalid_input", "Название клиента — не короче 2 символов.")
    if inn is not None and (not inn.isdigit() or len(inn) not in (10, 12)):
        raise ApiError("invalid_input", "ИНН — 10 или 12 цифр.")
    managers = visible_managers(store, caller.id)
    found = []
    for client in store.clients():
        if client["manager_id"] not in managers:
            continue
        if name is not None and not name_matches(name, client["name"]):
            continue
        if inn is not None and client["inn"] != inn:
            continue
        found.append(client_view(store, client))
    return list_response(found)


@router.get("/clients/{client_id}", summary="Карточка клиента")
async def get_client(
    client_id: str, caller: Caller = Depends(current_employee), store: Store = Depends(get_store)
) -> dict:
    return client_view(store, client_or_raise(store, caller, client_id))


class ManagersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_ids: list[str] = Field(..., max_length=100)


@router.post("/clients/managers", summary="Ответственные по списку клиентов (recs-service)")
async def client_managers(
    body: ManagersRequest,
    _: object = Depends(require_recs_service),
    store: Store = Depends(get_store),
) -> dict:
    items = []
    for client_id in body.client_ids:
        client = store.client(client_id)
        if client:
            items.append({"client_id": client_id, "name": client["name"],
                          "manager": store.employee_ref(client["manager_id"])})
    return list_response(items, SELECT_LIMIT)
