"""Заглушка пяти систем (порт 8040): REST API, пульт ведущего /admin, карточки /ui, Swagger /docs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from common.errors import install_error_handlers
from common.logging import setup_logging
from common.schemas import health_router
from fastapi import FastAPI

from .models import get_store
from .routes import admin, billing, calendar, confluence, crm, sd, ui
from .seed_loader import load_seed


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging("mock-systems")
    load_seed(get_store())
    yield


app = FastAPI(
    title="Заглушка систем демо-стенда",
    description="CRM, биллинг, Service-Desk, календарь, портал документации и пульт ведущего.",
    version="0.1.0",
    lifespan=lifespan,
)
install_error_handlers(app)
app.include_router(health_router("mock-systems"))
for module in (admin, crm, billing, sd, calendar, confluence, ui):
    app.include_router(module.router)
