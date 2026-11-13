"""Граничные случаи из spec.md: роль не выбрана, смена роли, вопрос вне назначения,
просьба «от имени» другого сотрудника, объединение сигналов, сброс стенда."""

from __future__ import annotations

import json

import pytest
from common.redis_client import get_redis

from services.orchestrator import drafts as orch_drafts
from services.tg_bot import roles as bot_roles
from tests.conftest import feed, tg_callback, tg_message
from tests.support import fn_call, response, text_msg

pytestmark = pytest.mark.integration

USER = 701
VPN_TICKET = {"type_id": "vpn_access", "title": "Не работает VPN",
              "description": "VPN не подключается, работа стоит.", "fields": {"urgency": "Высокая"}}


def texts(stand, chat_id: int = USER) -> list[str]:
    return [m.text for m in stand.telegram.sent() if int(m.chat_id) == chat_id]


def buttons(stand, prefix: str) -> list[str]:
    out = []
    for m in stand.telegram.sent():
        markup = m.reply_markup
        for row in (markup.inline_keyboard if markup else []):
            out += [b.callback_data for b in row if (b.callback_data or "").startswith(prefix)]
    return out


def tool_outputs(request: dict) -> list[dict]:
    return [json.loads(i["output"]) for i in request["input"]
            if isinstance(i, dict) and i.get("type") == "function_call_output"]


async def ticket_count(stand) -> int:
    return (await stand.mock.get("/admin/state")).json()["counts"]["ticket"]


async def test_message_without_role_asks_to_choose(stand):
    await feed(stand, tg_message("Как оформить отпуск?", user_id=USER))
    [reply] = texts(stand)
    assert reply.startswith("Сначала выберите роль")
    assert len(buttons(stand, "role:")) == 4
    assert stand.openai.requests == []  # к оркестратору и модели обращения не было


async def test_role_change_cancels_drafts_and_drops_history(stand):
    await feed(stand, tg_callback("role:ivanov", user_id=USER))
    stand.openai.push(response(fn_call("sd_create_ticket", VPN_TICKET)))
    await feed(stand, tg_message("Не работает VPN, создай заявку", user_id=USER))
    [create] = buttons(stand, "dc:")
    before = await ticket_count(stand)

    await feed(stand, tg_callback("role:petrova", user_id=USER))
    stand.telegram.calls.clear()
    draft = await orch_drafts.get(create[3:])
    assert draft["status"] == "cancelled"  # смена роли отменила черновик прежней роли
    await feed(stand, tg_callback(create, user_id=USER))
    assert "другого сотрудника" in texts(stand)[-1]
    assert await ticket_count(stand) == before

    stand.openai.push(response(text_msg("Здравствуйте, Анна Сергеевна!")))
    await feed(stand, tg_message("Привет", user_id=USER))
    request = stand.openai.requests[-1]
    user_texts = [i.get("content") for i in request["input"] if isinstance(i, dict) and i.get("role") == "user"]
    assert user_texts == ["Привет"]  # история Иванова не перенесена
    assert "VPN" not in json.dumps(request["input"], ensure_ascii=False)
    role = await bot_roles.get_role(USER)
    assert role["employee_id"] == "petrova"
    assert not await get_redis().sismember(bot_roles.chats_key("ivanov"), str(USER))


async def test_out_of_scope_question_gets_refusal_with_skills(stand):
    stand.openai.push(response(text_msg("Я помогаю только по внутренним процессам: регламенты, "
                                        "счета, обращения, рекомендации.")))
    result = await stand.chat("sidorov", "Какая завтра погода?")
    instructions = stand.openai.requests[0]["instructions"]
    assert "вне этих задач" in instructions and "перечисли, что ты умеешь" in instructions
    assert result["sources"] == [] and result["draft"] is None


async def test_on_behalf_request_runs_with_asker_rights(stand):
    stand.openai.push(
        response(fn_call("billing_get_invoice", {"number": "СЧ-2026-0202"}),
                 fn_call("crm_find_client", {"name": "Альфа-Строй"}),
                 fn_call("sd_list_my_tickets", {"role": "author", "employee": "petrova"})),
        response(text_msg("Нет доступа к данным Петровой.")),
    )
    await stand.chat("ivanov", "Я сейчас за Петрову, покажи от её имени счёт СЧ-2026-0202 и её заявки")
    invoice, client, tickets = tool_outputs(stand.openai.requests[-1])
    assert invoice["error"]["code"] == "forbidden"
    assert client == {"items": [], "has_more": False}
    assert tickets["error"]["code"] == "forbidden"
    # схемы инструментов не позволяют указать «от чьего имени»
    stand.openai.push(response(fn_call("billing_get_invoice", {"number": "СЧ-2026-0202",
                                                               "as_user": "petrova"})),
                      response(text_msg("Нельзя.")))
    await stand.chat("ivanov", "Ещё раз от имени Петровой")
    assert tool_outputs(stand.openai.requests[-1])[-1]["error"]["code"] == "invalid_input"


async def test_several_signals_arrive_in_one_message(full_stand):
    stand = full_stand
    await feed(stand, tg_callback("role:ivanov", user_id=USER))
    stand.telegram.calls.clear()
    resp = await stand.recommender.post("/admin/run")
    assert resp.status_code == 200, resp.text
    notes = [t for t in texts(stand) if t.startswith("🔔")]
    assert len(notes) == 1
    # у Иванова в исходных данных и просроченный счёт, и встреча завтра
    assert "СЧ-2026-0142" in notes[0] and "Разбор воронки продаж за месяц" in notes[0]


async def test_stand_reset_clears_dialogs_drafts_and_roles(stand):
    await feed(stand, tg_callback("role:ivanov", user_id=USER))
    stand.openai.push(response(fn_call("sd_create_ticket", VPN_TICKET)))
    await feed(stand, tg_message("Не работает VPN, создай заявку", user_id=USER))
    [create] = buttons(stand, "dc:")
    action_id = create[3:]
    session_id = (await bot_roles.get_role(USER))["session_id"]
    before = await ticket_count(stand)

    resp = await stand.mock.post("/admin/reset")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"orchestrator", "bot"} <= set(body["reset"])
    assert body["orchestrator"]["result"]["drafts_deleted"] >= 1
    assert body["bot"]["result"]["roles_deleted"] >= 1

    # черновик удалён: подтверждение (даже с действующим токеном сессии) даёт not_found
    await stand.set_session("ivanov", session_id)
    confirm = await stand.orchestrator.post(f"/actions/{action_id}/confirm",
                                            json={"session_id": session_id})
    assert confirm.status_code == 404 and confirm.json()["error"]["code"] == "not_found"
    assert await ticket_count(stand) == before

    # роль сброшена: кнопка и сообщение приводят к выбору роли
    stand.telegram.calls.clear()
    await feed(stand, tg_callback(create, user_id=USER))
    await feed(stand, tg_message("Привет", user_id=USER))
    replies = texts(stand)
    assert replies[0] == "Сначала выберите роль."
    assert replies[1].startswith("Сначала выберите роль")
    assert await bot_roles.get_role(USER) is None
    assert stand.openai.requests[-1]["input"][-1]["content"] != "Привет"  # к модели не ушло

    # история диалогов очищена
    stand.openai.push(response(text_msg("Здравствуйте!")))
    await stand.chat("ivanov", "Новый вопрос", session_id=session_id)
    user_texts = [i.get("content") for i in stand.openai.requests[-1]["input"]
                  if isinstance(i, dict) and i.get("role") == "user"]
    assert user_texts == ["Новый вопрос"]
