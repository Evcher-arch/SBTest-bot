"""Помощники контрактных тестов: прямой вызов MCP-точки с любым токеном (или без него)."""

from __future__ import annotations

from typing import Any

from common.http import make_client
from common.settings import get_settings
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from services.orchestrator.clients.mcp_client import _parse

ERROR_CODES = {"forbidden", "not_found", "invalid_input", "unavailable"}


async def mcp_call(server: str, name: str, args: dict, token: str | None) -> dict:
    """Вызвать инструмент MCP-точки /{server}/mcp; token=None — запрос без заголовка."""
    url = f"{get_settings().mcp_url.rstrip('/')}/{server}/mcp"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = StreamableHttpTransport(url, headers=headers,
                                        httpx_client_factory=lambda **kw: make_client(**kw))
    async with Client(transport, timeout=15) as client:
        result = await client.call_tool(name, args, raise_on_error=False, timeout=15)
    return _parse(result)


def error_code(body: dict) -> str | None:
    err = body.get("error")
    if isinstance(err, dict):
        assert err.get("code") in ERROR_CODES, body
        assert isinstance(err.get("message"), str) and err["message"], body
        return err["code"]
    return None


def assert_list(body: dict, item_keys: set[str]) -> list[dict]:
    assert "error" not in body, body
    assert isinstance(body.get("items"), list), body
    assert isinstance(body.get("has_more"), bool), body
    assert len(body["items"]) <= 20
    for item in body["items"]:
        missing = item_keys - set(item)
        assert not missing, f"нет полей {missing} в {item}"
    return body["items"]


def assert_rest_error(resp: Any, status: int | tuple[int, ...], code: str) -> None:
    statuses = status if isinstance(status, tuple) else (status,)
    assert resp.status_code in statuses, (resp.status_code, resp.text)
    body = resp.json()
    assert body["error"]["code"] == code, body
    assert body["error"]["message"], body
