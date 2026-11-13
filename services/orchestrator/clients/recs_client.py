"""Клиент сервиса рекомендаций (REST с токеном сотрудника)."""

from __future__ import annotations

from common.errors import code_from_status, error_body
from common.http import auth_header, make_client
from common.settings import get_settings


async def _request(method: str, path: str, token: str, **kwargs) -> dict:
    s = get_settings()
    try:
        async with make_client(base_url=s.recommender_url, timeout=s.tool_timeout_s) as client:
            resp = await client.request(method, path, headers=auth_header(token), **kwargs)
    except Exception:  # noqa: BLE001
        return error_body("unavailable", "Сервис рекомендаций временно недоступен.")
    try:
        data = resp.json()
    except ValueError:
        data = error_body(code_from_status(resp.status_code))
    return data


async def list_my(token: str, date: str | None = None) -> dict:
    params = {"date": date} if date else {}
    return await _request("GET", "/signals", token, params=params)


async def set_subscription(token: str, rule: str, enabled: bool) -> dict:
    return await _request("PUT", f"/subscriptions/{rule}", token, json={"enabled": enabled})
