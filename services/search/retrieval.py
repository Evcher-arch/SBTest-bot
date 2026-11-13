"""Гибридный поиск: векторный и полнотекстовый, объединение RRF, фильтр по группам из токена.

Фильтр allowed_groups && :groups применяется в SQL обоих запросов — до передачи модели (принцип III).
"""

from __future__ import annotations

from collections.abc import Sequence

from common.settings import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .db import get_engine, vector_literal
from .embeddings import embed

RRF_K = 60

VECTOR_SQL = text("""
    SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS similarity
    FROM chunk
    WHERE allowed_groups && CAST(:groups AS text[])
    ORDER BY embedding <=> CAST(:q AS vector)
    LIMIT :n
""")

# Слова запроса объединяются по ИЛИ: вопрос сотрудника длиннее заголовков регламента
FTS_SQL = text("""
    WITH q AS (
        SELECT NULLIF(replace(plainto_tsquery('russian', :t)::text, '&', '|'), '') AS expr
    )
    SELECT c.id, ts_rank_cd(c.tsv, to_tsquery('russian', q.expr)) AS rank
    FROM chunk c, q
    WHERE q.expr IS NOT NULL
      AND c.tsv @@ to_tsquery('russian', q.expr)
      AND c.allowed_groups && CAST(:groups AS text[])
    ORDER BY rank DESC
    LIMIT :n
""")

FETCH_SQL = text("""
    SELECT id, page_id, title, heading_path, url, updated_at, text
    FROM chunk WHERE id = ANY(CAST(:ids AS bigint[]))
""")


def rrf(rankings: Sequence[Sequence[int]], *, k: int = RRF_K, top_k: int | None = None) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: score = Σ 1 / (k + rank), rank с 1."""
    scores: dict[int, float] = {}
    first_seen: dict[int, int] = {}
    order = 0
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in first_seen:
                first_seen[doc_id] = order
                order += 1
    fused = sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
    return fused[:top_k] if top_k is not None else fused


async def search(query: str, groups: list[str], top_k: int = 5,
                 engine: AsyncEngine | None = None) -> list[dict]:
    if not groups:
        return []
    engine = engine or get_engine()
    s = get_settings()
    candidates = max(top_k * 4, 20)
    [query_vec] = await embed([query])
    async with engine.connect() as conn:
        vec_rows = (await conn.execute(VECTOR_SQL, {"q": vector_literal(query_vec), "groups": groups,
                                                    "n": candidates})).all()
        fts_rows = (await conn.execute(FTS_SQL, {"t": query, "groups": groups, "n": candidates})).all()
        vector_ids = [r.id for r in vec_rows if r.similarity >= s.search_min_similarity]
        fts_ids = [r.id for r in fts_rows]
        fused = rrf([vector_ids, fts_ids], top_k=top_k)
        if not fused:
            return []
        rows = (await conn.execute(FETCH_SQL, {"ids": [doc_id for doc_id, _ in fused]})).mappings().all()
    by_id = {r["id"]: r for r in rows}
    items = []
    for doc_id, score in fused:
        r = by_id.get(doc_id)
        if r is None:
            continue
        items.append({
            "title": r["title"], "section": r["heading_path"], "url": r["url"],
            "updated_at": r["updated_at"].isoformat(timespec="seconds"), "text": r["text"],
            "page_id": r["page_id"], "score": round(score, 5),
        })
    return items
