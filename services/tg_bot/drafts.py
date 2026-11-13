"""Черновик обращения: сообщение с кнопками «Создать» и «Отменить»."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from . import orchestrator_client
from .orchestrator_client import OrchestratorError, OrchestratorUnavailable
from .roles import get_role, roles_keyboard
from .tokens import issue_for_session

router = Router(name="drafts")

EXPIRED = "Черновик истёк или отменён — обращение не создано. Опишите проблему ещё раз, и я подготовлю новый черновик."


def draft_keyboard(action_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Создать", callback_data=f"dc:{action_id}"),
        InlineKeyboardButton(text="✖️ Отменить", callback_data=f"dx:{action_id}"),
    ]])


async def _session(callback: CallbackQuery) -> dict | None:
    role = await get_role(callback.from_user.id)
    if not role:
        await callback.answer()
        await callback.message.answer("Сначала выберите роль.", reply_markup=roles_keyboard())
        return None
    await issue_for_session(role["employee_id"], role["session_id"])
    return role


@router.callback_query(F.data.startswith("dc:"))
async def on_confirm(callback: CallbackQuery) -> None:
    action_id = callback.data[3:]
    role = await _session(callback)
    if role is None:
        return
    try:
        result = await orchestrator_client.confirm(action_id, role["session_id"])
    except OrchestratorError as exc:
        await callback.answer()
        if exc.code == "not_found":
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(EXPIRED)
        elif exc.code == "unavailable":
            await callback.message.answer(
                "Service-Desk сейчас недоступен, обращение не создано. Нажмите «Создать» ещё раз чуть позже.")
        else:
            await callback.message.answer(exc.message or "Не удалось создать обращение.")
        return
    except OrchestratorUnavailable:
        await callback.answer()
        await callback.message.answer("Сервис временно недоступен. Попробуйте нажать «Создать» ещё раз.")
        return
    await callback.answer("Готово")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — кнопки уже убраны предыдущим нажатием
        pass
    await callback.message.answer(result.get("text") or f"Обращение {result.get('number')} создано.")


@router.callback_query(F.data.startswith("dx:"))
async def on_cancel(callback: CallbackQuery) -> None:
    action_id = callback.data[3:]
    role = await _session(callback)
    if role is None:
        return
    try:
        await orchestrator_client.cancel(action_id, role["session_id"])
        text = "Черновик отменён, обращение не создано."
    except OrchestratorError as exc:
        text = EXPIRED if exc.code == "not_found" else (exc.message or "Не удалось отменить черновик.")
    except OrchestratorUnavailable:
        text = "Сервис временно недоступен. Попробуйте ещё раз."
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    await callback.message.answer(text)
