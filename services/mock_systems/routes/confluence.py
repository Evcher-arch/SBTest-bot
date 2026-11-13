"""Портал документации: список страниц и страница целиком. Только для индексатора."""

from __future__ import annotations

from common.errors import ApiError
from fastapi import APIRouter, Depends

from ..auth import outage_guard, require_indexer
from ..models import Store, get_store
from .common import card_url

router = APIRouter(
    prefix="/confluence",
    tags=["Портал документации"],
    dependencies=[Depends(outage_guard("confluence")), Depends(require_indexer)],
)


@router.get("/pages", summary="Список страниц с версиями")
async def list_pages(store: Store = Depends(get_store)) -> dict:
    items = [
        {"page_id": p["page_id"], "version": p["version"], "updated_at": p["updated_at"],
         "acl_updated_at": p["acl_updated_at"]}
        for p in store.pages()
    ]
    return {"items": items, "has_more": False}


@router.get("/pages/{page_id}", summary="Страница целиком")
async def get_page(page_id: str, store: Store = Depends(get_store)) -> dict:
    page = store.page(page_id)
    if page is None:
        raise ApiError("not_found", f"Страница {page_id} не найдена.")
    return page | {"url": card_url("pages", page_id)}
