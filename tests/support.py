"""Инфраструктура тестов: сервисы стенда в одном процессе и подмены внешних API.

- HostRouter направляет HTTP-запросы по имени хоста в ASGI-приложения сервисов
  (mock-systems, mcp-systems, search, orchestrator, recommender, tg-bot), а запросы
  к OpenAI — в FakeOpenAI. Сервисы создают клиенты через common.http.make_client,
  поэтому в тестах они общаются друг с другом без сети.
- FakeOpenAI подменяет Responses API (очередь заданных ответов модели) и Embeddings
  (детерминированные векторы по словам текста).
- FakeTelegramSession подменяет Bot API: записывает отправленные сообщения, индикаторы
  набора и кнопки.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from common.settings import get_settings

OPENAI_HOST = "openai.test"


class HostRouter(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.routes: dict[str, httpx.AsyncBaseTransport] = {}
        self.down: set[str] = set()

    def mount_app(self, url: str, app: Any) -> None:
        self.routes[urlsplit(url).hostname] = httpx.ASGITransport(app=app)

    def mount_transport(self, host: str, transport: httpx.AsyncBaseTransport) -> None:
        self.routes[host] = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.down or host not in self.routes:
            raise httpx.ConnectError(f"host {host} unavailable", request=request)
        return await self.routes[host].handle_async_request(request)


# --- OpenAI ------------------------------------------------------------------

_ids = itertools.count(1)


def fn_call(name: str, args: dict | str) -> dict:
    n = next(_ids)
    return {
        "type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": name,
        "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
        "status": "completed",
    }


def text_msg(text: str) -> dict:
    return {
        "type": "message", "id": f"msg_{next(_ids)}", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def response(*items: dict) -> dict:
    return {
        "id": f"resp_{next(_ids)}", "object": "response",
        "created_at": int(datetime.now(UTC).timestamp()), "status": "completed",
        "model": "test-model", "output": list(items), "parallel_tool_calls": True,
        "tool_choice": "auto", "tools": [], "temperature": 1.0, "top_p": 1.0,
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
    }


_WORD = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def embed_text(text: str, dims: int = 1536) -> list[float]:
    """Детерминированный «эмбеддинг»: хэш основ слов (первые 5 букв) в координаты."""
    vec = [0.0] * dims
    for word in _WORD.findall(text.lower()):
        stem = word[:5]
        h = int(hashlib.md5(stem.encode()).hexdigest(), 16)
        vec[h % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeOpenAI:
    """Очередь ответов Responses API. Элемент очереди — dict ответа, функция от запроса
    или исключение (имитация сбоя)."""

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.requests: list[dict] = []
        self.embedding_calls = 0
        self.default_text = "Готово."

    def push(self, *items: Any) -> None:
        self.queue.extend(items)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/embeddings"):
            self.embedding_calls += 1
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            data = [{"object": "embedding", "index": i, "embedding": embed_text(t)}
                    for i, t in enumerate(inputs)]
            return httpx.Response(200, json={"object": "list", "data": data, "model": body["model"],
                                             "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        if request.url.path.endswith("/responses"):
            self.requests.append(body)
            item = self.queue.pop(0) if self.queue else response(text_msg(self.default_text))
            if isinstance(item, Exception):
                raise item
            if callable(item):
                item = item(body)
            if isinstance(item, httpx.Response):
                return item
            return httpx.Response(200, json=item)
        return httpx.Response(404, json={"error": {"message": "not mocked"}})

    def all_request_text(self) -> str:
        return json.dumps(self.requests, ensure_ascii=False)


# --- Telegram ----------------------------------------------------------------


class FakeTelegramSession:
    """Сессия aiogram, записывающая вызовы Bot API вместо отправки в Telegram."""

    def __new__(cls):
        from aiogram.client.session.base import BaseSession

        class _Session(BaseSession):
            def __init__(self) -> None:
                super().__init__()
                self.calls: list[Any] = []
                self._msg_ids = itertools.count(1000)

            async def make_request(self, bot, method, timeout=None):  # type: ignore[override]
                from aiogram.methods import (
                    EditMessageReplyMarkup,
                    EditMessageText,
                    GetMe,
                    SendMessage,
                )
                from aiogram.types import Chat, Message, User

                if isinstance(method, GetMe):
                    return User(id=123456, is_bot=True, first_name="Демо", username="demo_stand_bot")
                self.calls.append(method)
                if isinstance(method, (SendMessage, EditMessageText)):
                    return Message(
                        message_id=next(self._msg_ids), date=datetime.now(UTC),
                        chat=Chat(id=int(method.chat_id or 0), type="private"),
                        text=method.text,
                    )
                if isinstance(method, EditMessageReplyMarkup):
                    return True
                return True

            async def close(self) -> None:
                return None

            async def stream_content(self, *args, **kwargs):  # pragma: no cover
                if False:
                    yield b""

            def sent(self) -> list[Any]:
                from aiogram.methods import SendMessage

                return [c for c in self.calls if isinstance(c, SendMessage)]

            def of_type(self, name: str) -> list[Any]:
                return [c for c in self.calls if type(c).__name__ == name]

        return _Session()


async def enter_lifespans(stack: AsyncExitStack, *apps: Any) -> None:
    """Войти в lifespan приложений в отдельной задаче и выйти из них в ней же.

    pytest-asyncio выполняет подготовку и завершение фикстуры в разных задачах, а
    lifespan MCP-сервера держит группу задач anyio, которую нужно закрыть в той же
    задаче, где она открыта. Поэтому lifespan живёт в собственной фоновой задаче.
    """
    import asyncio

    ready: asyncio.Future = asyncio.get_running_loop().create_future()
    stop = asyncio.Event()

    async def runner() -> None:
        try:
            async with AsyncExitStack() as inner:
                for app in apps:
                    await inner.enter_async_context(app.router.lifespan_context(app))
                ready.set_result(None)
                await stop.wait()
        except BaseException as exc:  # noqa: BLE001
            if not ready.done():
                ready.set_exception(exc)
            else:
                raise

    task = asyncio.create_task(runner())

    async def shutdown() -> None:
        stop.set()
        await task

    await ready
    stack.push_async_callback(shutdown)


def service_urls() -> dict[str, str]:
    s = get_settings()
    return {"mock": s.mock_url, "mcp": s.mcp_url, "search": s.search_url,
            "orchestrator": s.orchestrator_url, "recommender": s.recommender_url, "bot": s.bot_url}
