"""Оркестратор (порт 8000): цикл агента, черновики, формулировка рекомендаций, оценки."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from common.errors import install_error_handlers
from common.logging import setup_logging
from common.schemas import health_router
from fastapi import FastAPI

from .api import actions, chat, feedback, formulate
from .db import get_history
from .tracing import get_tracer

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging("orchestrator")
    await get_history().init()
    yield
    get_tracer().flush()


app = FastAPI(title="Оркестратор ИИ-помощника", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
app.include_router(health_router("orchestrator"))
for module in (chat, actions, formulate, feedback):
    app.include_router(module.router)
