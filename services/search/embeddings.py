"""Эмбеддинги OpenAI (OPENAI_EMBEDDING_MODEL, 1536 измерений), пакетный вызов."""

from __future__ import annotations

from common.http import make_client
from common.settings import get_settings
from openai import AsyncOpenAI

from .db import EMBEDDING_DIMS

BATCH = 64
_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncOpenAI(
            api_key=s.openai_api_key or "not-configured",
            base_url=s.openai_base_url or None,
            timeout=30,
            max_retries=2,
            http_client=make_client(timeout=30),
        )
    return _client


def reset_client() -> None:
    global _client
    _client = None


async def embed(texts: list[str]) -> list[list[float]]:
    model = get_settings().openai_embedding_model
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        resp = await get_client().embeddings.create(model=model, input=batch, dimensions=EMBEDDING_DIMS)
        vectors.extend(item.embedding for item in sorted(resp.data, key=lambda d: d.index))
    return vectors
