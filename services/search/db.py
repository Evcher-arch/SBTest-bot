"""Схема индекса регламентов в PostgreSQL + pgvector."""

from __future__ import annotations

from common.settings import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

EMBEDDING_DIMS = 1536

DDL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""
    CREATE TABLE IF NOT EXISTS chunk (
        id bigserial PRIMARY KEY,
        page_id text NOT NULL,
        page_version integer NOT NULL,
        space text NOT NULL,
        title text NOT NULL,
        heading_path text NOT NULL,
        url text NOT NULL,
        updated_at timestamptz NOT NULL,
        text text NOT NULL,
        embedding vector({EMBEDDING_DIMS}) NOT NULL,
        tsv tsvector GENERATED ALWAYS AS (
            to_tsvector('russian', title || ' ' || heading_path || ' ' || text)
        ) STORED,
        allowed_groups text[] NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS chunk_embedding_hnsw ON chunk USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX IF NOT EXISTS chunk_tsv_gin ON chunk USING gin (tsv)",
    "CREATE INDEX IF NOT EXISTS chunk_groups_gin ON chunk USING gin (allowed_groups)",
    "CREATE INDEX IF NOT EXISTS chunk_page_id ON chunk (page_id)",
    """
    CREATE TABLE IF NOT EXISTS index_state (
        page_id text PRIMARY KEY,
        page_version integer NOT NULL,
        acl_updated_at timestamptz NOT NULL,
        indexed_at timestamptz NOT NULL DEFAULT now()
    )
    """,
]

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


async def init_db(engine: AsyncEngine | None = None) -> None:
    engine = engine or get_engine()
    async with engine.begin() as conn:
        # Несколько процессов могут стартовать одновременно — сериализуем создание схемы
        await conn.execute(text("SELECT pg_advisory_xact_lock(424242)"))
        for statement in DDL:
            await conn.execute(text(statement))


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"
