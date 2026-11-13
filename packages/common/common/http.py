"""Фабрика HTTP-клиентов. Тесты подменяют транспорт, чтобы сервисы общались в одном процессе."""

from __future__ import annotations

from typing import Any

import httpx

_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def get_transport() -> httpx.AsyncBaseTransport | None:
    return _transport


def make_client(**kwargs: Any) -> httpx.AsyncClient:
    if _transport is not None and "transport" not in kwargs:
        kwargs["transport"] = _transport
    kwargs.setdefault("timeout", 10.0)
    return httpx.AsyncClient(**kwargs)


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
