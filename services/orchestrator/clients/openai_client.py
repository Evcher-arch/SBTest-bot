"""Клиент модели: OpenAI SDK, Responses API с function tools.

Используется обёртка Langfuse для OpenAI, чтобы обращения к модели (токены, стоимость)
попадали в трассу как generation. Если обёртка недоступна — обычный клиент.
"""

from __future__ import annotations

from typing import Any

from common.http import make_client
from common.settings import get_settings

_client: Any = None


def _client_class() -> Any:
    try:
        from langfuse.openai import AsyncOpenAI  # интеграция SDK: generation в трассе
    except Exception:  # noqa: BLE001
        from openai import AsyncOpenAI
    return AsyncOpenAI


def get_openai() -> Any:
    global _client
    if _client is None:
        s = get_settings()
        cls = _client_class()
        _client = cls(
            api_key=s.openai_api_key or "not-configured",
            base_url=s.openai_base_url or None,
            timeout=s.answer_timeout_s,
            max_retries=1,
            http_client=make_client(timeout=s.answer_timeout_s),
        )
    return _client


def set_openai(client: Any) -> None:
    global _client
    _client = client


def _reasoning() -> dict[str, Any]:
    """Уровень рассуждения для моделей с reasoning; пусто — параметр не передаётся."""
    effort = get_settings().openai_reasoning_effort.strip()
    return {"reasoning": {"effort": effort}} if effort else {}


async def respond(*, instructions: str, input: list[dict], tools: list[dict],
                  name: str = "agent-step") -> Any:
    s = get_settings()
    kwargs: dict[str, Any] = {
        "model": s.openai_model,
        "instructions": instructions,
        "input": input,
        "tools": tools,
        "parallel_tool_calls": True,
    }
    kwargs |= _reasoning()
    if _client_class().__module__.startswith("langfuse"):
        kwargs["name"] = name
    return await get_openai().responses.create(**kwargs)


async def complete_text(*, instructions: str, text: str, name: str = "formulate") -> str:
    s = get_settings()
    kwargs: dict[str, Any] = {"model": s.openai_model, "instructions": instructions, "input": text}
    kwargs |= _reasoning()
    if _client_class().__module__.startswith("langfuse"):
        kwargs["name"] = name
    response = await get_openai().responses.create(**kwargs)
    return (response.output_text or "").strip()
