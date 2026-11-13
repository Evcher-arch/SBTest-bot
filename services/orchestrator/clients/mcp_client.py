"""MCP-клиент оркестратора: четыре точки систем (/crm, /billing, /sd, /calendar).

К точке /service (сервисные выборки) оркестратор не подключается. Данные систем
не кэшируются: каждый вызов — новый запрос с токеном сотрудника, взятым в момент вызова.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from common.errors import error_body
from common.http import auth_header, make_client
from common.settings import get_settings
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

log = logging.getLogger(__name__)

SERVERS = ("crm", "billing", "sd", "calendar")
SYSTEM_NAMES = {"crm": "CRM", "billing": "Биллинг", "sd": "Service-Desk", "calendar": "Календарь"}


def _factory(**kwargs: Any):
    return make_client(**kwargs)


def _parse(result: Any) -> dict:
    data = getattr(result, "structured_content", None)
    if isinstance(data, dict):
        if set(data) == {"result"} and isinstance(data["result"], dict):
            return data["result"]
        return data
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except ValueError:
                return error_body("unavailable", text[:200])
    return error_body("unavailable", "Пустой ответ системы.")


async def call_tool(server: str, name: str, args: dict, token: str) -> dict:
    if server not in SERVERS:
        raise ValueError(f"orchestrator must not call MCP endpoint {server!r}")
    s = get_settings()
    url = f"{s.mcp_url.rstrip('/')}/{server}/mcp"
    transport = StreamableHttpTransport(url, headers=auth_header(token), httpx_client_factory=_factory)
    try:
        async with Client(transport, timeout=s.tool_timeout_s) as client:
            result = await client.call_tool(name, args, raise_on_error=False,
                                            timeout=s.tool_timeout_s)
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP %s/%s failed: %s", server, name, type(exc).__name__)
        return error_body("unavailable", f"Система «{SYSTEM_NAMES[server]}» временно недоступна.")
    if getattr(result, "is_error", False):
        parsed = _parse(result)
        if "error" in parsed:
            return parsed
        return error_body("invalid_input", "Система отклонила параметры вызова.")
    return _parse(result)
