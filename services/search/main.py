"""Сервис поиска по регламентам (порт 8010): POST /search, POST /admin/reindex, индексатор по расписанию."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from common.errors import ApiError, install_error_handlers
from common.jwt import TokenClaims, bearer_from_header, verify_token
from common.logging import setup_logging
from common.schemas import health_router
from common.settings import get_settings
from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from . import indexer, retrieval
from .db import init_db

log = logging.getLogger(__name__)


async def _safe_index() -> None:
    try:
        await indexer.run_once()
    except Exception:  # noqa: BLE001 — индексатор повторит попытку по расписанию
        log.warning("indexer run failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("search")
    await init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_safe_index, "interval", seconds=get_settings().index_interval_s,
                      id="indexer", max_instances=1, coalesce=True)
    scheduler.start()
    first = asyncio.create_task(_safe_index())
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)
    first.cancel()


app = FastAPI(title="Поиск по регламентам", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
app.include_router(health_router("search"))


async def employee_claims(request: Request) -> TokenClaims:
    claims = verify_token(bearer_from_header(request.headers.get("authorization")))
    if claims.role != "employee":
        raise ApiError("forbidden", "Поиск доступен только сотрудникам.")
    return claims


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=2, max_length=500)
    top_k: int = Field(5, ge=1, le=8)


@app.post("/search")
async def search(body: SearchRequest, claims: TokenClaims = Depends(employee_claims)) -> dict:
    try:
        items = await retrieval.search(body.query, claims.groups, body.top_k)
    except Exception as exc:  # noqa: BLE001
        log.warning("search failed: %s", type(exc).__name__, exc_info=True)
        raise ApiError("unavailable", "Поиск по регламентам временно недоступен.") from exc
    return {"items": items, "has_more": False}


@app.post("/admin/reindex")
async def reindex() -> dict:
    try:
        return await indexer.run_once()
    except Exception as exc:  # noqa: BLE001
        log.warning("reindex failed", exc_info=True)
        raise ApiError("unavailable", "Переиндексация не выполнена: портал документации недоступен.") from exc
