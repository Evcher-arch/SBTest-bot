"""Диалог: индикатор набора, вызов /chat, ответ целиком с источниками, черновиком и оценками."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import Message

from . import orchestrator_client
from .drafts import draft_keyboard
from .feedback import feedback_keyboard
from .orchestrator_client import OrchestratorError, OrchestratorUnavailable
from .render import format_answer, format_draft, split_message
from .roles import CHOOSE, get_role, roles_keyboard
from .tokens import issue_for_session

log = logging.getLogger(__name__)
router = Router(name="dialog")

UNAVAILABLE = "Сервис временно недоступен. Попробуйте повторить вопрос через минуту."
DIGEST_QUESTION = "Что мне важно сделать?"
TYPING_INTERVAL_S = 4.0


async def _typing(bot: Bot, chat_id: int) -> None:
    while True:
        await asyncio.sleep(TYPING_INTERVAL_S)
        with contextlib.suppress(Exception):
            await bot.send_chat_action(chat_id, ChatAction.TYPING)


async def send_response(bot: Bot, chat_id: int, response: dict) -> None:
    parts = split_message(format_answer(response))
    trace_id = response.get("trace_id")
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        markup = feedback_keyboard(trace_id) if last and trace_id else None
        await bot.send_message(chat_id, part, reply_markup=markup, disable_web_page_preview=True)
    draft = response.get("draft")
    if draft:
        await bot.send_message(chat_id, format_draft(draft), reply_markup=draft_keyboard(draft["action_id"]))


async def ask(message: Message, bot: Bot, text: str) -> None:
    role = await get_role(message.from_user.id)
    if not role:
        await message.answer("Сначала выберите роль. " + CHOOSE, reply_markup=roles_keyboard())
        return
    # Индикатор набора — сразу после получения сообщения и до ответа
    with contextlib.suppress(Exception):
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    typing = asyncio.create_task(_typing(bot, message.chat.id))
    try:
        await issue_for_session(role["employee_id"], role["session_id"])
        response = await orchestrator_client.chat(role["session_id"], role["employee_id"], text)
    except (OrchestratorUnavailable, OrchestratorError) as exc:
        log.warning("chat failed: %s", type(exc).__name__)
        response = None
    finally:
        typing.cancel()
    if response is None:
        await message.answer(UNAVAILABLE)
        return
    await send_response(bot, message.chat.id, response)


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    role = await get_role(message.from_user.id)
    if not role:
        await message.answer(CHOOSE, reply_markup=roles_keyboard())
        return
    try:
        await orchestrator_client.reset_session(role["session_id"])
        await message.answer("Диалог начат заново: история очищена, ожидающие черновики отменены.")
    except Exception:  # noqa: BLE001
        await message.answer(UNAVAILABLE)


@router.message(Command("digest"))
async def cmd_digest(message: Message, bot: Bot) -> None:
    await ask(message, bot, DIGEST_QUESTION)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, bot: Bot) -> None:
    await ask(message, bot, message.text)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Я понимаю только текстовые сообщения. /help — что я умею.")
