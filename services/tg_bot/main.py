"""Процесс бота: aiogram 3 (long polling) и HTTP-сервер на порту 8443 (/health, /notify, /admin/reset)."""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from common.errors import install_error_handlers
from common.logging import setup_logging
from common.schemas import health_router
from common.settings import get_settings
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from . import dialog, drafts, feedback, notify, roles

log = logging.getLogger(__name__)


def create_bot(session=None) -> Bot | None:
    s = get_settings()
    if not s.telegram_bot_token:
        log.warning("TELEGRAM_BOT_TOKEN не задан: бот не подключается к Telegram")
        return None
    if session is None:
        session = AiohttpSession(proxy=s.telegram_proxy) if s.telegram_proxy else AiohttpSession()
    return Bot(token=s.telegram_bot_token, session=session, default=DefaultBotProperties(parse_mode=None))


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    # Порядок важен: команды и кнопки — до обработчика произвольного текста
    dp.include_routers(roles.router, drafts.router, feedback.router, dialog.router)
    return dp


class NotifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1, max_length=8000)
    urls: list[str] = Field(default_factory=list)


def create_http_app(bot: Bot | None) -> FastAPI:
    app = FastAPI(title="Telegram-бот: внутренний API", version="0.1.0")
    install_error_handlers(app)
    app.include_router(health_router("tg-bot"))
    app.state.bot = bot

    @app.post("/notify")
    async def notify_endpoint(body: NotifyRequest) -> dict:
        delivered = await notify.deliver(app.state.bot, body.employee_id, body.text, body.urls)
        return {"delivered": delivered}

    @app.post("/admin/reset")
    async def admin_reset() -> dict:
        return {"roles_deleted": await notify.reset_state()}

    return app


async def main() -> None:
    setup_logging("tg-bot")
    bot = create_bot()
    app = create_http_app(bot)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8443, log_level="warning"))
    tasks = [asyncio.create_task(server.serve())]
    if bot is not None:
        dp = create_dispatcher()
        tasks.append(asyncio.create_task(dp.start_polling(bot, handle_signals=False)))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
