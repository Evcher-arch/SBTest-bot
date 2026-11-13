"""Клиент сервиса поиска (REST с токеном сотрудника)."""

from __future__ import annotations

from common.errors import code_from_status, error_body
from common.http import auth_header, make_client
from common.settings import get_settings


async def search(token: str, query: str, top_k: int = 5) -> dict:
    s = get_settings()
    try:
        async with make_client(base_url=s.search_url, timeout=s.tool_timeout_s) as client:
            resp = await client.post("/search", json={"query": query, "top_k": top_k},
                                     headers=auth_header(token))
    except Exception:  # noqa: BLE001
        return error_body("unavailable", "Поиск по регламентам временно недоступен.")
    if resp.status_code >= 400:
        try:
            return resp.json()
        except ValueError:
            return error_body(code_from_status(resp.status_code))
    return resp.json()
