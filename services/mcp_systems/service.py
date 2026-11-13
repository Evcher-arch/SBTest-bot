"""Точка сервисных выборок /service. Только для токена role: recs-service.

Оркестратор к этой точке не подключается; в реестре инструментов агента этих имён нет (принцип I).
"""

from __future__ import annotations

from common.errors import ApiError
from common.jwt import verify_token
from fastmcp import FastMCP

from .http import Handler, call_mock, contract_tool, current_token


def _service_only(handler: Handler) -> Handler:
    async def guarded(args: dict) -> dict:
        claims = verify_token(current_token())
        if claims.role != "recs-service":
            raise ApiError("forbidden", "Выборки доступны только сервису рекомендаций.")
        return await handler(args)

    return guarded


async def billing_select_invoices(args: dict) -> dict:
    return await call_mock("billing", "GET", "/billing/invoices/select", params=args)


async def crm_select_client_managers(args: dict) -> dict:
    return await call_mock("crm", "POST", "/crm/clients/managers", json={"client_ids": args["client_ids"]})


async def sd_select_stale_tickets(args: dict) -> dict:
    return await call_mock("sd", "GET", "/sd/tickets/select", params=args)


async def calendar_select_events(args: dict) -> dict:
    params: list[tuple[str, str]] = [("date", args["date"])]
    params += [("users", u) for u in args.get("users") or []]
    return await call_mock("calendar", "GET", "/calendar/events/select", params=params)


def build() -> FastMCP:
    server = FastMCP("service", instructions="Сервисные выборки для сервиса рекомендаций.")
    for name, handler in (
        ("billing_select_invoices", billing_select_invoices),
        ("crm_select_client_managers", crm_select_client_managers),
        ("sd_select_stale_tickets", sd_select_stale_tickets),
        ("calendar_select_events", calendar_select_events),
    ):
        server.add_tool(contract_tool(name, _service_only(handler)))
    return server
