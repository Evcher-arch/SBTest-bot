"""Кнопки оценки 👍 / 👎 под ответом; оценка уходит в Langfuse к трассе."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from . import orchestrator_client

log = logging.getLogger(__name__)
router = Router(name="feedback")


def feedback_keyboard(trace_id: str, selected: int | None = None) -> InlineKeyboardMarkup:
    up = "👍" + (" ✓" if selected == 1 else "")
    down = "👎" + (" ✓" if selected == -1 else "")
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=up, callback_data=f"fb:1:{trace_id}"),
        InlineKeyboardButton(text=down, callback_data=f"fb:-1:{trace_id}"),
    ]])


@router.callback_query(F.data.startswith("fb:"))
async def on_feedback(callback: CallbackQuery) -> None:
    _, value, trace_id = callback.data.split(":", 2)
    try:
        await orchestrator_client.feedback(trace_id, int(value))
    except Exception:  # noqa: BLE001 — оценка не критична для диалога
        log.warning("feedback failed")
    await callback.answer("Спасибо за оценку!")
    try:
        await callback.message.edit_reply_markup(reply_markup=feedback_keyboard(trace_id, int(value)))
    except Exception:  # noqa: BLE001 — сообщение могло быть изменено ранее
        pass
