"""Проверка токена и правила доступа заглушки.

Из токена берутся только sub и role. Группы, руководитель и подчинённые определяются
по собственной таблице Employee (принцип I: доступ решает система по своей ролевой модели).
Порядок проверки — сначала существование (not_found), затем права (forbidden).
"""

from __future__ import annotations

from dataclasses import dataclass

from common.errors import ApiError
from common.jwt import TokenClaims, bearer_from_header, verify_token
from fastapi import Depends, Request

from .models import Store, get_store

SYSTEM_NAMES = {
    "crm": "CRM",
    "billing": "Биллинг",
    "sd": "Service-Desk",
    "calendar": "Календарь",
    "confluence": "Портал документации",
}


def outage_guard(system: str):
    """Зависимость маршрутизатора: при включённом отказе система отвечает 503 unavailable."""

    async def _guard(store: Store = Depends(get_store)) -> None:
        if system in store.outages:
            raise ApiError("unavailable", f"Система «{SYSTEM_NAMES[system]}» временно недоступна.")

    return _guard


async def get_claims(request: Request) -> TokenClaims:
    return verify_token(bearer_from_header(request.headers.get("authorization")))


@dataclass(frozen=True)
class Caller:
    id: str
    name: str
    groups: list[str]


async def current_employee(
    claims: TokenClaims = Depends(get_claims), store: Store = Depends(get_store)
) -> Caller:
    if claims.role != "employee":
        raise ApiError("forbidden", "Метод доступен только сотрудникам.")
    emp = store.employee(claims.sub)
    if emp is None:
        raise ApiError("forbidden", "Сотрудник не найден в системе.")
    return Caller(id=emp["id"], name=emp["name"], groups=emp["groups"])


def require_role(role: str):
    async def _dep(claims: TokenClaims = Depends(get_claims)) -> TokenClaims:
        if claims.role != role:
            raise ApiError("forbidden", "Метод доступен только сервисной учётной записи.")
        return claims

    return _dep


require_recs_service = require_role("recs-service")
require_indexer = require_role("indexer")


def visible_managers(store: Store, employee_id: str) -> set[str]:
    """Менеджеры, чьих клиентов видит сотрудник: он сам и его подчинённые."""
    return {employee_id, *store.subordinates(employee_id)}


def can_see_client(store: Store, employee_id: str, client: dict) -> bool:
    """Ответственный менеджер и его руководитель."""
    owner = client["manager_id"]
    return owner == employee_id or store.manager_of(owner) == employee_id


def can_see_ticket(store: Store, employee_id: str, ticket: dict) -> bool:
    """Автор, исполнитель, руководитель автора или исполнителя."""
    if employee_id in (ticket["author_id"], ticket["assignee_id"]):
        return True
    for person in (ticket["author_id"], ticket["assignee_id"]):
        if person and store.manager_of(person) == employee_id:
            return True
    return False


def client_or_raise(store: Store, caller: Caller, client_id: str) -> dict:
    client = store.client(client_id)
    if client is None:
        raise ApiError("not_found", f"Клиент {client_id} не найден.")
    if not can_see_client(store, caller.id, client):
        raise ApiError("forbidden", "Нет доступа к этому клиенту.")
    return client
