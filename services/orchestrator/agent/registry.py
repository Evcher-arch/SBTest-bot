"""Реестр инструментов агента: белый список, схемы, исполнители.

Ровно 11 инструментов из contracts/agent-tools.md; сервисных выборок здесь нет.
Единственный изменяющий инструмент — sd_create_ticket: в цикле агента он не исполняется,
а превращается в черновик (см. loop.py и drafts.py).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from common.errors import ApiError, error_body
from common.tool_schemas import AGENT_TOOLS, model_schema, validate_args

from ..clients import mcp_client, recs_client, search_client
from ..clients.tokens import session_token


@dataclass
class ToolContext:
    session_id: str
    employee_id: str


Executor = Callable[[ToolContext, dict], Awaitable[dict]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # search, read, write, recs
    executor: Executor
    intercept: bool = False  # True — не исполнять в цикле, а готовить черновик


async def _token_or_error(ctx: ToolContext) -> str | None:
    return await session_token(ctx.session_id)


def _no_token() -> dict:
    return error_body("forbidden", "Сессия истекла: отправьте сообщение ещё раз.")


def _mcp(server: str, name: str) -> Executor:
    async def run(ctx: ToolContext, args: dict) -> dict:
        token = await _token_or_error(ctx)
        if not token:
            return _no_token()
        return await mcp_client.call_tool(server, name, args, token)

    return run


async def _search(ctx: ToolContext, args: dict) -> dict:
    token = await _token_or_error(ctx)
    if not token:
        return _no_token()
    return await search_client.search(token, args["query"], args.get("top_k", 5))


async def _recs_list(ctx: ToolContext, args: dict) -> dict:
    token = await _token_or_error(ctx)
    if not token:
        return _no_token()
    return await recs_client.list_my(token, args.get("date"))


async def _recs_set(ctx: ToolContext, args: dict) -> dict:
    token = await _token_or_error(ctx)
    if not token:
        return _no_token()
    return await recs_client.set_subscription(token, args["rule"], args["enabled"])


REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("search_regulations", "search", _search),
        ToolSpec("crm_find_client", "read", _mcp("crm", "crm_find_client")),
        ToolSpec("billing_list_invoices", "read", _mcp("billing", "billing_list_invoices")),
        ToolSpec("billing_get_invoice", "read", _mcp("billing", "billing_get_invoice")),
        ToolSpec("sd_list_my_tickets", "read", _mcp("sd", "sd_list_my_tickets")),
        ToolSpec("sd_get_ticket", "read", _mcp("sd", "sd_get_ticket")),
        ToolSpec("sd_list_ticket_types", "read", _mcp("sd", "sd_list_ticket_types")),
        ToolSpec("sd_create_ticket", "write", _mcp("sd", "sd_create_ticket"), intercept=True),
        ToolSpec("calendar_list_my_events", "read", _mcp("calendar", "calendar_list_my_events")),
        ToolSpec("recs_list_my", "recs", _recs_list),
        ToolSpec("recs_set_subscription", "recs", _recs_set),
    )
}


def is_allowed(name: str) -> bool:
    return name in REGISTRY


def validate(name: str, args: Any, *, from_model: bool = True) -> dict:
    """Проверка параметров по схеме. Для вызова модели скрытые поля недопустимы."""
    if not is_allowed(name):
        raise ApiError("invalid_input", f"Инструмент {name} не разрешён.")
    if from_model and isinstance(args, dict):
        hidden = set(AGENT_TOOLS[name].get("hidden_from_model", []))
        if hidden & set(args):
            raise ApiError("invalid_input", f"Недопустимые параметры: {', '.join(sorted(hidden & set(args)))}.")
        if REGISTRY[name].intercept:
            probe = dict(args, idempotency_key="validation-only-key")
            validate_args(name, probe)
            return args
    return validate_args(name, args)


def model_tools() -> list[dict]:
    """Описание инструментов для Responses API (function tools)."""
    return [
        {
            "type": "function",
            "name": name,
            "description": AGENT_TOOLS[name]["description"],
            "parameters": model_schema(name),
            "strict": False,
        }
        for name in REGISTRY
    ]


async def execute(name: str, ctx: ToolContext, args: dict) -> dict:
    return await REGISTRY[name].executor(ctx, args)
