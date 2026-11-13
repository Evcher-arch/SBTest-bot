"""Клиент оркестратора для бота (httpx, таймаут 25 с)."""

from __future__ import annotations

from typing import Any

from common.http import make_client
from common.settings import get_settings

TIMEOUT_S = 25.0


class OrchestratorUnavailable(Exception):
    """Сеть, таймаут или ошибка 5xx без тела в едином формате."""


class OrchestratorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


async def _post(path: str, payload: dict | None = None) -> dict[str, Any]:
    s = get_settings()
    try:
        async with make_client(base_url=s.orchestrator_url, timeout=TIMEOUT_S) as client:
            resp = await client.post(path, json=payload or {})
    except Exception as exc:  # noqa: BLE001
        raise OrchestratorUnavailable(type(exc).__name__) from exc
    try:
        body = resp.json()
    except ValueError as exc:
        raise OrchestratorUnavailable(f"HTTP {resp.status_code}") from exc
    if resp.status_code >= 400:
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            raise OrchestratorError(error.get("code", "unavailable"), error.get("message", ""))
        raise OrchestratorUnavailable(f"HTTP {resp.status_code}")
    return body


async def chat(session_id: str, employee_id: str, text: str) -> dict:
    return await _post("/chat", {"session_id": session_id, "employee_id": employee_id, "text": text})


async def confirm(action_id: str, session_id: str) -> dict:
    return await _post(f"/actions/{action_id}/confirm", {"session_id": session_id})


async def cancel(action_id: str, session_id: str) -> dict:
    return await _post(f"/actions/{action_id}/cancel", {"session_id": session_id})


async def reset_session(session_id: str) -> dict:
    return await _post(f"/sessions/{session_id}/reset")


async def feedback(trace_id: str, value: int) -> dict:
    return await _post("/feedback", {"trace_id": trace_id, "value": value})
