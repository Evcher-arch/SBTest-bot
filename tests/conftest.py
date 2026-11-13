"""Общие фикстуры тестов.

Тесты не обращаются к OpenAI и Telegram: OpenAI подменяется FakeOpenAI (httpx-транспорт),
Telegram — FakeTelegramSession (сессия aiogram). Сервисы стенда поднимаются в этом же
процессе как ASGI-приложения и общаются через HostRouter.

Тесты с маркером integration используют PostgreSQL и Redis из compose: отдельную базу
<имя>_test и отдельный номер базы Redis, чтобы не затрагивать данные работающего стенда.
"""

from __future__ import annotations

import os
import re

# --- окружение тестов: до импорта сервисов --------------------------------
os.environ.update({
    "JWT_SECRET": "test-secret-0123456789abcdef0123456789abcdef",
    "OPENAI_API_KEY": "test-key",
    "OPENAI_BASE_URL": "http://openai.test/v1",
    "OPENAI_MODEL": "test-model",
    "LANGFUSE_PUBLIC_KEY": "",
    "LANGFUSE_SECRET_KEY": "",
    "LANGFUSE_TRACING_ENABLED": "false",
    "TELEGRAM_BOT_TOKEN": "123456:TEST-TOKEN",
    "TELEGRAM_PROXY": "",
    "MOCK_PUBLIC_URL": "http://localhost:8040",
    "ORCHESTRATOR_URL": "http://orchestrator:8000",
    "SEARCH_URL": "http://search:8010",
    "MCP_URL": "http://mcp-systems:8020",
    "RECOMMENDER_URL": "http://recommender:8030",
    "MOCK_URL": "http://mock-systems:8040",
    "BOT_URL": "http://tg-bot:8443",
    "SEARCH_MIN_SIMILARITY": "0.05",
    "INDEX_INTERVAL_S": "3600",
    "ANSWER_TIMEOUT_S": "18",
})
_db = os.environ.get("DATABASE_URL", "postgresql+asyncpg://stand:stand@localhost:5432/stand")
if not _db.rstrip("/").endswith("_test"):
    _db = re.sub(r"/([^/]+)$", r"/\1_test", _db)
os.environ["DATABASE_URL"] = _db
_redis = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
os.environ["REDIS_URL"] = re.sub(r"/\d+$", "/15", _redis) if re.search(r"/\d+$", _redis) else _redis + "/15"

from contextlib import AsyncExitStack  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import Any  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from common import http as common_http  # noqa: E402
from common import redis_client  # noqa: E402
from common.jwt import issue_service_token, issue_token  # noqa: E402
from common.settings import get_settings, reset_settings  # noqa: E402

reset_settings()

from tests.support import (  # noqa: E402
    OPENAI_HOST,
    FakeOpenAI,
    FakeTelegramSession,
    HostRouter,
    enter_lifespans,
)

EMPLOYEES = ("ivanov", "petrova", "sidorov", "kuznetsova")


# --- токены ------------------------------------------------------------------


def employee_token(employee_id: str, **kwargs: Any) -> str:
    from common import directory

    emp = directory.get_employee(employee_id)
    return issue_token(employee_id, role="employee", name=emp.name if emp else employee_id,
                       groups=emp.groups if emp else [],
                       subordinates=directory.subordinates_of(employee_id), **kwargs)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tokens() -> dict[str, str]:
    """Токены четырёх сотрудников и сервисные токены."""
    out = {e: employee_token(e) for e in EMPLOYEES}
    out["recs-service"] = issue_service_token("recs-service")
    out["indexer"] = issue_service_token("indexer")
    return out


@pytest.fixture
def expired_token() -> str:
    import time

    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode({"sub": "ivanov", "role": "employee", "groups": ["all"], "iat": now - 1200,
                         "exp": now - 600}, get_settings().jwt_secret, algorithm="HS256")


# --- подмена Langfuse -------------------------------------------------------


class FakeObservation:
    def __init__(self, store: FakeLangfuse, name: str, as_type: str, input: Any, metadata: Any,
                 parent: FakeObservation | None, trace_id: str) -> None:
        self.store, self.name, self.as_type = store, name, as_type
        self.input, self.metadata, self.output = input, metadata, None
        self.parent, self.trace_id = parent, trace_id
        self.level: str | None = None
        self.status_message: str | None = None
        self.ended = False

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeLangfuse:
    """Клиент Langfuse в памяти: записывает трассы, наблюдения и оценки."""

    def __init__(self, fail: bool = False) -> None:
        self.observations: list[FakeObservation] = []
        self.scores: list[dict] = []
        self.fail = fail
        self._stack: list[FakeObservation] = []
        self._counter = 0

    def start_as_current_observation(self, *, name: str, as_type: str = "span", input: Any = None,
                                     metadata: Any = None, **_: Any):
        if self.fail:
            raise ConnectionError("langfuse down")
        store = self

        class _CM:
            def __enter__(self_inner):
                parent = store._stack[-1] if store._stack else None
                if parent is None:
                    store._counter += 1
                    trace_id = f"{store._counter:032x}"
                else:
                    trace_id = parent.trace_id
                obs = FakeObservation(store, name, as_type, input, metadata, parent, trace_id)
                store.observations.append(obs)
                store._stack.append(obs)
                return obs

            def __exit__(self_inner, *exc):
                obs = store._stack.pop()
                obs.ended = True
                return False

        return _CM()

    def get_current_trace_id(self) -> str | None:
        return self._stack[-1].trace_id if self._stack else None

    def create_score(self, **kwargs: Any) -> None:
        if self.fail:
            raise ConnectionError("langfuse down")
        self.scores.append(kwargs)

    def flush(self) -> None:
        if self.fail:
            raise ConnectionError("langfuse down")

    def traces(self) -> list[FakeObservation]:
        return [o for o in self.observations if o.parent is None]

    def children(self, trace_id: str) -> list[FakeObservation]:
        return [o for o in self.observations if o.trace_id == trace_id and o.parent is not None]

    def dump(self) -> str:
        import json

        return json.dumps([{k: v for k, v in vars(o).items() if k not in ("store", "parent")}
                           for o in self.observations], ensure_ascii=False, default=str)


# --- стенд в одном процессе -------------------------------------------------


@dataclass
class Stand:
    router: HostRouter
    openai: FakeOpenAI
    langfuse: FakeLangfuse
    clients: dict[str, httpx.AsyncClient] = field(default_factory=dict)
    telegram: Any = None
    bot: Any = None
    dispatcher: Any = None

    def __getattr__(self, name: str) -> httpx.AsyncClient:
        try:
            return self.__dict__["clients"][name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    async def set_session(self, employee_id: str, session_id: str | None = None) -> str:
        """Как бот: выпустить токен сотрудника и положить в Redis token:{session_id}."""
        from services.tg_bot.tokens import issue_for_session

        session_id = session_id or f"s-{employee_id}"
        await issue_for_session(employee_id, session_id)
        return session_id

    async def chat(self, employee_id: str, text: str, session_id: str | None = None) -> dict:
        session_id = await self.set_session(employee_id, session_id)
        resp = await self.orchestrator.post("/chat", json={"session_id": session_id,
                                                           "employee_id": employee_id, "text": text})
        assert resp.status_code == 200, resp.text
        return resp.json()


_DISPATCHER = None


def _dispatcher():
    global _DISPATCHER
    if _DISPATCHER is None:
        from services.tg_bot.main import create_dispatcher

        _DISPATCHER = create_dispatcher()
    return _DISPATCHER


async def _build_stand(*, with_db: bool, langfuse: FakeLangfuse | None = None,
                       redis_client_obj: Any = None) -> tuple[Stand, AsyncExitStack]:
    from services.mcp_systems.main import create_app as create_mcp
    from services.mock_systems.main import app as mock_app
    from services.orchestrator import db as orch_db
    from services.orchestrator.clients import openai_client
    from services.orchestrator.main import app as orch_app
    from services.orchestrator.tracing import Tracer, set_tracer
    from services.search import embeddings
    from services.tg_bot.main import create_bot, create_http_app

    router = HostRouter()
    fake = FakeOpenAI()
    router.mount_transport(OPENAI_HOST, fake.transport())
    common_http.set_transport(router)
    openai_client.set_openai(None)
    embeddings.reset_client()
    redis_client.set_redis(redis_client_obj or fakeredis.aioredis.FakeRedis(decode_responses=True))
    lf = langfuse or FakeLangfuse()
    set_tracer(Tracer(client=lf))

    s = get_settings()
    mcp_app = create_mcp()
    session = FakeTelegramSession()
    bot = create_bot(session=session)
    bot_http = create_http_app(bot)
    apps: dict[str, Any] = {"mock": (s.mock_url, mock_app), "mcp": (s.mcp_url, mcp_app),
                            "orchestrator": (s.orchestrator_url, orch_app), "bot": (s.bot_url, bot_http)}
    if with_db:
        from services.recommender.main import app as recs_app
        from services.search.main import app as search_app

        orch_db.set_history(orch_db.PgHistory())
        apps["search"] = (s.search_url, search_app)
        apps["recommender"] = (s.recommender_url, recs_app)
    else:
        orch_db.set_history(orch_db.MemoryHistory())

    stack = AsyncExitStack()
    for url, app in apps.values():
        router.mount_app(url, app)
    await enter_lifespans(stack, *(app for _, app in apps.values()))
    stand = Stand(router=router, openai=fake, langfuse=lf, telegram=session, bot=bot,
                  dispatcher=_dispatcher())
    for name, (url, _) in apps.items():
        client = httpx.AsyncClient(transport=router, base_url=url, timeout=30)
        await stack.enter_async_context(client)
        stand.clients[name] = client
    return stand, stack


@pytest.fixture
async def stand():
    """Стенд без PostgreSQL: заглушка, MCP, оркестратор (история в памяти), бот; Redis — fakeredis."""
    stand, stack = await _build_stand(with_db=False)
    try:
        yield stand
    finally:
        await stack.aclose()
        common_http.set_transport(None)


async def _ensure_test_database() -> None:
    import asyncpg

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    base, name = url.rsplit("/", 1)
    conn = await asyncpg.connect(f"{base}/postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _clean_database() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            for table in ("message", "chunk", "index_state", "signal", "subscription"):
                await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    finally:
        await engine.dispose()


def _reset_engines() -> None:
    from services.recommender import db as recs_db
    from services.search import db as search_db

    search_db._engine = None
    recs_db._engine = None


@pytest.fixture
async def full_stand():
    """Полный стенд: + поиск и рекомендации на PostgreSQL, Redis из compose (база 15)."""
    from redis.asyncio import Redis

    try:
        await _ensure_test_database()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL недоступен: {exc}")
    await _clean_database()
    _reset_engines()
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    try:
        await redis.flushdb()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis недоступен: {exc}")
    stand, stack = await _build_stand(with_db=True, redis_client_obj=redis)
    try:
        resp = await stand.search.post("/admin/reindex")
        assert resp.status_code == 200, resp.text
        yield stand
    finally:
        await stack.aclose()
        common_http.set_transport(None)
        _reset_engines()
        await redis.aclose()


# --- Telegram-обновления ----------------------------------------------------

_update_ids = iter(range(1, 10**9))


def tg_message(text: str, user_id: int = 501, chat_id: int | None = None):
    from aiogram.types import Chat, Message, MessageEntity, Update, User

    chat_id = chat_id or user_id
    entities = None
    if text.startswith("/"):
        entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]
    msg = Message(message_id=next(_update_ids), date=datetime.now(UTC),
                  chat=Chat(id=chat_id, type="private"),
                  from_user=User(id=user_id, is_bot=False, first_name="Тест"),
                  text=text, entities=entities)
    return Update(update_id=next(_update_ids), message=msg)


def tg_callback(data: str, user_id: int = 501, chat_id: int | None = None, message_id: int = 1):
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    chat_id = chat_id or user_id
    msg = Message(message_id=message_id, date=datetime.now(UTC),
                  chat=Chat(id=chat_id, type="private"), text="…")
    cq = CallbackQuery(id=str(next(_update_ids)), from_user=User(id=user_id, is_bot=False, first_name="Тест"),
                       chat_instance="ci", message=msg, data=data)
    return Update(update_id=next(_update_ids), callback_query=cq)


async def feed(stand: Stand, update) -> None:
    await stand.dispatcher.feed_update(stand.bot, update)
