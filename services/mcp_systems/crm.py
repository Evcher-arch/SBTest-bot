"""MCP-сервер CRM: crm_find_client."""

from __future__ import annotations

from fastmcp import FastMCP

from .http import call_mock, contract_tool


async def crm_find_client(args: dict) -> dict:
    return await call_mock("crm", "GET", "/crm/clients", params=args)


def build() -> FastMCP:
    server = FastMCP("crm", instructions="CRM: поиск клиентов, доступных сотруднику.")
    server.add_tool(contract_tool("crm_find_client", crm_find_client))
    return server
