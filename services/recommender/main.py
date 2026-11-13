"""Сервис рекомендаций (порт 8030): расписание правил, сводка, подписки, ручной запуск, сброс."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from common.errors import ApiError, install_error_handlers
from common.jwt import TokenClaims, bearer_from_header, verify_token
from common.logging import setup_logging
from common.schemas import health_router
from common.settings import get_settings
from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict

from . import db, engine
from .rules import RULE_IDS, load_rules

log = logging.getLogger(__name__)


async def _scheduled(rule_ids: list[str]) -> None:
    rules = [r for r in load_rules() if r.id in rule_ids]
    try:
        result = await engine.run(rules)
        if result["signals_created"]:
            log.info("rules %s: %s", rule_ids, result)
    except Exception:  # noqa: BLE001
        log.warning("scheduled run failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("recommender")
    await db.init_db()
    scheduler = AsyncIOScheduler()
    by_interval: dict[int, list[str]] = {}
    for rule in load_rules():
        by_interval.setdefault(rule.interval_seconds, []).append(rule.id)
    for interval, ids in by_interval.items():
        # Правила с одинаковым интервалом проходят вместе — сигналы получателя в одном сообщении
        scheduler.add_job(_scheduled, "interval", seconds=interval, args=[ids],
                          id=f"rules-{interval}", max_instances=1, coalesce=True)
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Сервис рекомендаций", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
app.include_router(health_router("recommender"))


async def employee(request: Request) -> TokenClaims:
    claims = verify_token(bearer_from_header(request.headers.get("authorization")))
    if claims.role != "employee":
        raise ApiError("forbidden", "Метод доступен только сотрудникам.")
    return claims


@app.get("/signals", summary="Актуальные сигналы сотрудника (ничего не записывает и не отправляет)")
async def signals(date_: str | None = Query(None, alias="date"),
                  claims: TokenClaims = Depends(employee)) -> dict:
    try:
        day = date.fromisoformat(date_) if date_ else get_settings().today()
    except ValueError as exc:
        raise ApiError("invalid_input", "date: ожидается дата ГГГГ-ММ-ДД.") from exc
    candidates = await engine.candidates_for(day)
    items = [{"rule": c.rule, "text": c.text, "object": c.object_label, "url": c.url}
             for c in candidates if c.recipient_id == claims.sub]
    return {"items": items[:20], "has_more": len(items) > 20}


class SubscriptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@app.put("/subscriptions/{rule}", summary="Включить или отключить правило для себя")
async def set_subscription(rule: str, body: SubscriptionBody,
                           claims: TokenClaims = Depends(employee)) -> dict:
    if rule not in RULE_IDS:
        raise ApiError("invalid_input", f"Неизвестное правило: {rule}. Допустимы {', '.join(RULE_IDS)}.")
    await db.set_subscription(claims.sub, rule, body.enabled)
    return {"items": await db.subscriptions(claims.sub, list(RULE_IDS)), "has_more": False}


@app.post("/admin/run", summary="Немедленная проверка всех правил и рассылка")
async def admin_run() -> dict:
    return await engine.run()


@app.post("/admin/reset", summary="Удалить отправленные сигналы и подписки")
async def admin_reset() -> dict:
    return await db.reset()
