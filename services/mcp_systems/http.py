"""Общая часть MCP-серверов систем.

Токен читается из заголовка Authorization входящего MCP-запроса (в FastMCP 3 он скрыт по
умолчанию, поэтому include_all=True) и передаётся в заглушку без изменений. Сотрудник
в параметрах инструментов не передаётся. Ответы 400/401/403/404/503 и сетевые ошибки
превращаются в {error: {code, message}}.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from common.errors import ApiError, code_from_status, error_body
from common.http import auth_header, make_client
from common.jwt import bearer_from_header
from common.settings import get_settings
from common.tool_schemas import AGENT_TOOLS, SERVICE_SELECTIONS, validate_args
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools.base import Tool, ToolResult
from pydantic import Field

log = logging.getLogger(__name__)

SYSTEM_NAMES = {"crm": "CRM", "billing": "Биллинг", "sd": "Service-Desk", "calendar": "Календарь"}

# Подмена источника токена в контрактных тестах (без HTTP-запроса MCP)
_token_override: Callable[[], str | None] | None = None


def set_token_source(source: Callable[[], str | None] | None) -> None:
    global _token_override
    _token_override = source


def current_token() -> str | None:
    if _token_override is not None:
        return _token_override()
    headers = get_http_headers(include_all=True)
    return bearer_from_header(headers.get("authorization"))


async def call_mock(system: str, method: str, path: str, *, params: Any = None,
                    json: Any = None, headers: dict[str, str] | None = None) -> dict:
    token = current_token()
    if not token:
        return error_body("forbidden", "Требуется токен доступа.")
    s = get_settings()
    try:
        async with make_client(base_url=s.mock_url, timeout=s.tool_timeout_s) as client:
            resp = await client.request(method, path, params=params, json=json,
                                        headers=auth_header(token) | (headers or {}))
    except Exception as exc:  # noqa: BLE001
        log.warning("mock %s %s failed: %s", method, path, type(exc).__name__)
        return error_body("unavailable", f"Система «{SYSTEM_NAMES[system]}» временно недоступна.")
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code >= 400:
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            if body["error"].get("code") == "unavailable":
                return error_body("unavailable", f"Система «{SYSTEM_NAMES[system]}» временно недоступна.")
            return body
        return error_body(code_from_status(resp.status_code))
    return body if isinstance(body, dict) else {"result": body}


Handler = Callable[[dict], Awaitable[dict]]


class ContractTool(Tool):
    """Инструмент с JSON-схемой из контракта (additionalProperties: false) и проверкой входа."""

    handler: Handler = Field(exclude=True)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            validate_args(self.name, arguments or {})
        except ApiError as exc:
            return ToolResult(structured_content=exc.body())
        try:
            result = await self.handler(arguments or {})
        except ApiError as exc:
            result = exc.body()
        return ToolResult(structured_content=result)


def contract_tool(name: str, handler: Handler) -> ContractTool:
    spec = AGENT_TOOLS.get(name) or SERVICE_SELECTIONS[name]
    return ContractTool(name=name, description=spec["description"], parameters=spec["schema"],
                        handler=handler)
