"""Клиенты сервиса рекомендаций: MCP /service (сервисный токен), /formulate, /notify."""

from __future__ import annotations

import json
from typing import Any

from common.http import auth_header, make_client
from common.jwt import issue_service_token
from common.settings import get_settings
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


class SelectionError(Exception):
    pass


async def select(name: str, args: dict) -> list[dict]:
    """Вызов сервисной выборки с токеном role: recs-service."""
    s = get_settings()
    transport = StreamableHttpTransport(
        f"{s.mcp_url.rstrip('/')}/service/mcp",
        headers=auth_header(issue_service_token("recs-service")),
        httpx_client_factory=lambda **kw: make_client(**kw),
    )
    async with Client(transport, timeout=15) as client:
        result = await client.call_tool(name, args, raise_on_error=False, timeout=15)
    data: Any = result.structured_content
    if data is None:
        for block in result.content or []:
            if getattr(block, "text", None):
                data = json.loads(block.text)
                break
    if not isinstance(data, dict) or "error" in data:
        raise SelectionError(f"{name}: {data}")
    return data.get("items", [])


async def formulate(recipient_name: str, signals: list[dict]) -> str | None:
    s = get_settings()
    try:
        async with make_client(base_url=s.orchestrator_url, timeout=25) as client:
            resp = await client.post("/formulate", json={"recipient_name": recipient_name,
                                                         "signals": signals})
        if resp.status_code >= 400:
            return None
        return resp.json().get("text")
    except Exception:  # noqa: BLE001 — при сбое используется шаблон
        return None


async def notify(employee_id: str, text: str, urls: list[str]) -> int | None:
    """Возвращает число чатов, куда доставлено, или None при сбое доставки."""
    s = get_settings()
    try:
        async with make_client(base_url=s.bot_url, timeout=15) as client:
            resp = await client.post("/notify", json={"employee_id": employee_id, "text": text,
                                                      "urls": urls})
        if resp.status_code >= 400:
            return None
        return int(resp.json().get("delivered", 0))
    except Exception:  # noqa: BLE001
        return None
