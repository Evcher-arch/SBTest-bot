"""История диалога (таблица message) в PostgreSQL. Токен в content не записывается."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from common.settings import get_settings
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

metadata = MetaData()

message = Table(
    "message",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("session_id", String, nullable=False, index=True),
    Column("employee_id", String, nullable=False),
    Column("role", String, nullable=False),  # user, assistant, tool
    Column("content", JSONB, nullable=False),
    Column("trace_id", String),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


@dataclass
class Message:
    role: str
    content: dict[str, Any]


class History(Protocol):
    async def init(self) -> None: ...
    async def load(self, session_id: str, limit: int) -> list[Message]: ...
    async def append(self, session_id: str, employee_id: str, role: str,
                     content: dict[str, Any], trace_id: str | None) -> None: ...
    async def delete_session(self, session_id: str) -> int: ...
    async def clear(self) -> int: ...


class PgHistory:
    def __init__(self, url: str | None = None) -> None:
        self._url = url or get_settings().database_url
        self._engine: AsyncEngine | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self._url, pool_pre_ping=True)
        return self._engine

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    async def load(self, session_id: str, limit: int) -> list[Message]:
        query = (
            select(message.c.role, message.c.content)
            .where(message.c.session_id == session_id, message.c.role.in_(("user", "assistant")))
            .order_by(message.c.id.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [Message(role=r.role, content=r.content) for r in reversed(rows)]

    async def append(self, session_id: str, employee_id: str, role: str,
                     content: dict[str, Any], trace_id: str | None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(message.insert().values(
                session_id=session_id, employee_id=employee_id, role=role,
                content=content, trace_id=trace_id,
            ))

    async def delete_session(self, session_id: str) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(delete(message).where(message.c.session_id == session_id))
        return result.rowcount or 0

    async def clear(self) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(delete(message))
        return result.rowcount or 0

    async def dump(self) -> list[dict]:
        """Все строки таблицы (для проверки, что токен не попал в историю)."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(message))).mappings().all()
        return [dict(r) for r in rows]


class MemoryHistory:
    """История в памяти — для модульных тестов цикла агента."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def init(self) -> None:
        return None

    async def load(self, session_id: str, limit: int) -> list[Message]:
        rows = [r for r in self.rows if r["session_id"] == session_id and r["role"] in ("user", "assistant")]
        return [Message(role=r["role"], content=r["content"]) for r in rows[-limit:]]

    async def append(self, session_id: str, employee_id: str, role: str,
                     content: dict[str, Any], trace_id: str | None) -> None:
        self.rows.append({"session_id": session_id, "employee_id": employee_id, "role": role,
                          "content": content, "trace_id": trace_id,
                          "created_at": datetime.now(UTC)})

    async def delete_session(self, session_id: str) -> int:
        before = len(self.rows)
        self.rows = [r for r in self.rows if r["session_id"] != session_id]
        return before - len(self.rows)

    async def clear(self) -> int:
        count = len(self.rows)
        self.rows = []
        return count

    async def dump(self) -> list[dict]:
        return list(self.rows)


_history: History | None = None


def get_history() -> History:
    global _history
    if _history is None:
        _history = PgHistory()
    return _history


def set_history(history: History | None) -> None:
    global _history
    _history = history
