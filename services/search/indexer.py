"""Индексатор: подхват изменённых и удалённых страниц портала документации.

Сравнивает version и acl_updated_at с index_state; фрагменты изменённой страницы
заменяются одной транзакцией; фрагменты исчезнувших страниц удаляются.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from urllib.parse import quote

from common.http import auth_header, make_client
from common.jwt import issue_service_token
from common.settings import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .chunking import chunk_markdown
from .db import get_engine, vector_literal
from .embeddings import embed

log = logging.getLogger(__name__)
_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    """Один проход индексатора за раз в пределах цикла событий.

    Lock создаётся на каждый цикл событий: lock, оставшийся от закрытого цикла
    (например, прерванный проход по расписанию), не блокирует новый цикл.
    """
    loop = asyncio.get_running_loop()
    lock = _locks.get(id(loop))
    if lock is None:
        _locks.clear()
        lock = _locks[id(loop)] = asyncio.Lock()
    return lock

INSERT = text("""
    INSERT INTO chunk (page_id, page_version, space, title, heading_path, url, updated_at, text,
                       embedding, allowed_groups)
    VALUES (:page_id, :page_version, :space, :title, :heading_path, :url, :updated_at, :text,
            CAST(:embedding AS vector), CAST(:allowed_groups AS text[]))
""")
UPSERT_STATE = text("""
    INSERT INTO index_state (page_id, page_version, acl_updated_at, indexed_at)
    VALUES (:page_id, :page_version, :acl_updated_at, now())
    ON CONFLICT (page_id) DO UPDATE
    SET page_version = EXCLUDED.page_version, acl_updated_at = EXCLUDED.acl_updated_at,
        indexed_at = now()
""")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def _fetch(client, path: str) -> dict:
    resp = await client.get(path)
    resp.raise_for_status()
    return resp.json()


async def run_once(engine: AsyncEngine | None = None) -> dict:
    engine = engine or get_engine()
    async with _lock():
        s = get_settings()
        headers = auth_header(issue_service_token("indexer"))
        async with make_client(base_url=s.mock_url, headers=headers, timeout=20) as client:
            pages = (await _fetch(client, "/confluence/pages"))["items"]
            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    "SELECT page_id, page_version, acl_updated_at FROM index_state"))).all()
            state = {r.page_id: (r.page_version, r.acl_updated_at) for r in rows}

            changed = [
                p for p in pages
                if p["page_id"] not in state
                or state[p["page_id"]][0] != p["version"]
                or state[p["page_id"]][1] != _dt(p["acl_updated_at"])
            ]
            updated = 0
            for meta in changed:
                page = await _fetch(client, f"/confluence/pages/{quote(meta['page_id'], safe='')}")
                chunks = chunk_markdown(page["title"], page["body"])
                vectors = await embed([f"{page['title']}. {c.heading_path}\n{c.text}" for c in chunks])
                async with engine.begin() as conn:
                    await conn.execute(text("DELETE FROM chunk WHERE page_id = :p"), {"p": page["page_id"]})
                    for chunk, vector in zip(chunks, vectors, strict=True):
                        await conn.execute(INSERT, {
                            "page_id": page["page_id"], "page_version": page["version"],
                            "space": page["space"], "title": page["title"],
                            "heading_path": chunk.heading_path, "url": page["url"],
                            "updated_at": _dt(page["updated_at"]), "text": chunk.text,
                            "embedding": vector_literal(vector),
                            "allowed_groups": page["allowed_groups"],
                        })
                    await conn.execute(UPSERT_STATE, {
                        "page_id": page["page_id"], "page_version": page["version"],
                        "acl_updated_at": _dt(page["acl_updated_at"]),
                    })
                updated += 1

        gone = set(state) - {p["page_id"] for p in pages}
        if gone:
            async with engine.begin() as conn:
                for page_id in gone:
                    await conn.execute(text("DELETE FROM chunk WHERE page_id = :p"), {"p": page_id})
                    await conn.execute(text("DELETE FROM index_state WHERE page_id = :p"), {"p": page_id})
        async with engine.connect() as conn:
            total = (await conn.execute(text("SELECT count(*) FROM chunk"))).scalar_one()
    if updated or gone:
        log.info("indexer: updated=%s deleted=%s chunks=%s", updated, len(gone), total)
    return {"pages_updated": updated, "pages_deleted": len(gone), "chunks": total}
