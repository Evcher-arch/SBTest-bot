"""MCP-сервер биллинга: billing_list_invoices, billing_get_invoice."""

from __future__ import annotations

from urllib.parse import quote

from fastmcp import FastMCP

from .http import call_mock, contract_tool


async def billing_list_invoices(args: dict) -> dict:
    params = {"client_id": args["client_id"]}
    if args.get("status"):
        params["status"] = args["status"]
    period = args.get("period") or {}
    if period.get("from"):
        params["from"] = period["from"]
    if period.get("to"):
        params["to"] = period["to"]
    return await call_mock("billing", "GET", "/billing/invoices", params=params)


async def billing_get_invoice(args: dict) -> dict:
    return await call_mock("billing", "GET", f"/billing/invoices/{quote(args['number'], safe='')}")


def build() -> FastMCP:
    server = FastMCP("billing", instructions="Биллинг: счета клиентов и оплаты.")
    server.add_tool(contract_tool("billing_list_invoices", billing_list_invoices))
    server.add_tool(contract_tool("billing_get_invoice", billing_get_invoice))
    return server
