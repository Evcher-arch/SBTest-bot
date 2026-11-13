"""MCP-сервер Service-Desk: чтение обращений, типы, создание с ключом идемпотентности."""

from __future__ import annotations

from urllib.parse import quote

from fastmcp import FastMCP

from .http import call_mock, contract_tool


async def sd_list_my_tickets(args: dict) -> dict:
    return await call_mock("sd", "GET", "/sd/tickets", params=args)


async def sd_get_ticket(args: dict) -> dict:
    return await call_mock("sd", "GET", f"/sd/tickets/{quote(args['number'], safe='')}")


async def sd_list_ticket_types(args: dict) -> dict:
    return await call_mock("sd", "GET", "/sd/ticket-types")


async def sd_create_ticket(args: dict) -> dict:
    body = {k: args[k] for k in ("type_id", "title", "description")}
    body["fields"] = args.get("fields") or {}
    return await call_mock("sd", "POST", "/sd/tickets", json=body,
                           headers={"Idempotency-Key": args["idempotency_key"]})


def build() -> FastMCP:
    server = FastMCP("sd", instructions="Service-Desk: обращения сотрудника и создание обращений.")
    for name, handler in (
        ("sd_list_my_tickets", sd_list_my_tickets),
        ("sd_get_ticket", sd_get_ticket),
        ("sd_list_ticket_types", sd_list_ticket_types),
        ("sd_create_ticket", sd_create_ticket),
    ):
        server.add_tool(contract_tool(name, handler))
    return server
