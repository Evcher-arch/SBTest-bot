"""MCP-сервер календаря: calendar_list_my_events (только встречи владельца токена)."""

from __future__ import annotations

from datetime import datetime, timedelta

from common.errors import ApiError
from fastmcp import FastMCP

from .http import call_mock, contract_tool


async def calendar_list_my_events(args: dict) -> dict:
    try:
        start = datetime.fromisoformat(args["from"])
        end = datetime.fromisoformat(args["to"])
    except ValueError as exc:
        raise ApiError("invalid_input", "from и to — дата-время ISO 8601.") from exc
    if start.tzinfo is not None and end.tzinfo is not None and end - start > timedelta(days=31):
        raise ApiError("invalid_input", "Интервал — не больше 31 дня.")
    return await call_mock("calendar", "GET", "/calendar/events",
                           params={"from": args["from"], "to": args["to"]})


def build() -> FastMCP:
    server = FastMCP("calendar", instructions="Календарь: встречи сотрудника.")
    server.add_tool(contract_tool("calendar_list_my_events", calendar_list_my_events))
    return server
