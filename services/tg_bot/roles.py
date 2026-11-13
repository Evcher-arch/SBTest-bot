"""Выбор демо-сотрудника: /start, /role и кнопки выбора роли."""

from __future__ import annotations

import logging
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from common import directory
from common.redis_client import get_redis

from . import orchestrator_client

log = logging.getLogger(__name__)
router = Router(name="roles")

WARNING = (
    "⚠️ Это демонстрационный стенд ИИ-помощника. Все данные вымышленные. "
    "Не вводите реальные сведения о клиентах, сотрудниках и компании: запросы обрабатывает "
    "внешняя языковая модель."
)
HELP = (
    "Я помогаю по внутренним процессам:\n"
    "• отвечаю на вопросы по регламентам со ссылкой на источник;\n"
    "• показываю клиентов, счета, обращения в Service-Desk и встречи;\n"
    "• готовлю обращение в Service-Desk — оно создаётся только по кнопке «Создать»;\n"
    "• присылаю рекомендации: что важно сделать.\n\n"
    "Команды: /role — сменить сотрудника, /digest — что мне важно сделать, "
    "/reset — начать диалог заново, /help — эта справка."
)
CHOOSE = "Выберите, от имени какого сотрудника вы работаете:"


def role_key(tg_user_id: int) -> str:
    return f"role:{tg_user_id}"


def chats_key(employee_id: str) -> str:
    return f"chats:{employee_id}"


def roles_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{e.name.split()[0]} — {e.position}", callback_data=f"role:{e.id}")]
        for e in directory.all_employees()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_role(tg_user_id: int) -> dict | None:
    data = await get_redis().hgetall(role_key(tg_user_id))
    return data or None


async def set_role(tg_user_id: int, chat_id: int, employee_id: str) -> dict:
    """Новая роль — новая сессия; прежняя сессия сбрасывается (история и черновики)."""
    redis = get_redis()
    previous = await get_role(tg_user_id)
    if previous:
        await redis.srem(chats_key(previous["employee_id"]), str(chat_id))
        try:
            await orchestrator_client.reset_session(previous["session_id"])
        except Exception:  # noqa: BLE001 — недоступность оркестратора не мешает смене роли
            log.warning("previous session reset failed")
    session_id = uuid.uuid4().hex
    await redis.hset(role_key(tg_user_id), mapping={"employee_id": employee_id, "session_id": session_id,
                                                    "chat_id": str(chat_id)})
    await redis.sadd(chats_key(employee_id), str(chat_id))
    return {"employee_id": employee_id, "session_id": session_id}


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(WARNING)
    await message.answer(CHOOSE, reply_markup=roles_keyboard())


@router.message(Command("role"))
async def cmd_role(message: Message) -> None:
    await message.answer(CHOOSE, reply_markup=roles_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.callback_query(F.data.startswith("role:"))
async def on_role(callback: CallbackQuery) -> None:
    employee_id = callback.data.split(":", 1)[1]
    emp = directory.get_employee(employee_id)
    if emp is None:
        await callback.answer("Неизвестный сотрудник", show_alert=True)
        return
    await set_role(callback.from_user.id, callback.message.chat.id, employee_id)
    await callback.answer()
    await callback.message.answer(
        f"Вы работаете как {emp.name} ({emp.position}). Задайте вопрос.\n\n{HELP}"
    )
