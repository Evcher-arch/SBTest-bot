"""Цикл агента с подменной моделью: белый список, схемы, лимиты, отказоустойчивость."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from common.settings import reset_settings

from services.orchestrator.agent import prompts
from services.orchestrator.clients import openai_client, search_client
from services.orchestrator.tracing import Tracer, set_tracer
from tests.conftest import FakeLangfuse
from tests.support import fn_call, response, text_msg


def _tool_outputs(request: dict) -> list[dict]:
    return [json.loads(i["output"]) for i in request["input"]
            if isinstance(i, dict) and i.get("type") == "function_call_output"]


async def test_tool_outside_whitelist_is_not_executed(stand):
    stand.openai.push(
        response(fn_call("billing_select_invoices", {"status": "overdue"})),
        response(text_msg("Не могу выполнить.")),
    )
    result = await stand.chat("ivanov", "Покажи все просроченные счета компании")
    assert result["text"] == "Не могу выполнить."
    [output] = _tool_outputs(stand.openai.requests[1])
    assert output["error"]["code"] == "invalid_input"
    assert "не разрешён" in output["error"]["message"]


async def test_invalid_arguments_give_invalid_input(stand):
    stand.openai.push(
        response(fn_call("billing_list_invoices", {"client_id": "C001", "employee": "petrova"})),
        response(fn_call("crm_find_client", {"name": "Ромашка", "inn": "7701234567"})),
        response(fn_call("crm_find_client", "{not json")),
        response(text_msg("Уточните запрос.")),
    )
    await stand.chat("ivanov", "Счета Ромашки")
    codes = [_tool_outputs(r)[-1]["error"]["code"] for r in stand.openai.requests[1:4]]
    assert codes == ["invalid_input"] * 3


async def test_hidden_idempotency_key_cannot_be_set_by_model(stand):
    stand.openai.push(
        response(fn_call("sd_create_ticket", {"type_id": "other", "title": "x", "description": "y",
                                              "idempotency_key": "attacker-key-123"})),
        response(text_msg("Ок.")),
    )
    result = await stand.chat("ivanov", "Создай заявку")
    assert result["draft"] is None
    assert _tool_outputs(stand.openai.requests[1])[0]["error"]["code"] == "invalid_input"


async def test_model_tools_have_no_employee_parameter(stand):
    stand.openai.push(response(text_msg("Здравствуйте!")))
    await stand.chat("ivanov", "Привет")
    tools = stand.openai.requests[0]["tools"]
    assert len(tools) == 11
    for tool in tools:
        props = tool["parameters"]["properties"]
        assert not {"employee_id", "user", "user_id", "as_user", "on_behalf_of"} & set(props)
        assert tool["parameters"]["additionalProperties"] is False
    create = next(t for t in tools if t["name"] == "sd_create_ticket")
    assert "idempotency_key" not in create["parameters"]["properties"]


async def test_loop_stops_at_max_steps(stand):
    steps = 6
    stand.openai.push(*[response(fn_call("sd_list_ticket_types", {})) for _ in range(steps + 3)])
    result = await stand.chat("ivanov", "Зациклись")
    assert result["text"] == prompts.STEP_LIMIT
    assert len(stand.openai.requests) == steps


async def test_model_failure_gives_canned_message(stand):
    stand.openai.push(httpx.Response(500, json={"error": {"message": "boom"}}),
                      httpx.Response(500, json={"error": {"message": "boom"}}))
    result = await stand.chat("ivanov", "Как оформить отпуск?")
    assert result["text"] == prompts.MODEL_UNAVAILABLE
    assert result["sources"] == [] and result["draft"] is None


async def test_answer_timeout_gives_canned_message(stand, monkeypatch):
    monkeypatch.setenv("ANSWER_TIMEOUT_S", "0.3")
    reset_settings()

    async def slow(**_):
        await asyncio.sleep(2)

    monkeypatch.setattr(openai_client, "respond", slow)
    try:
        result = await stand.chat("ivanov", "Вопрос")
    finally:
        monkeypatch.delenv("ANSWER_TIMEOUT_S")
        reset_settings()
    assert result["text"] == prompts.MODEL_UNAVAILABLE


async def test_tool_timeout_is_reported_to_model(stand, monkeypatch):
    monkeypatch.setenv("TOOL_TIMEOUT_S", "0.2")
    reset_settings()

    async def slow_search(*_a, **_k):
        await asyncio.sleep(1)

    monkeypatch.setattr(search_client, "search", slow_search)
    stand.openai.push(response(fn_call("search_regulations", {"query": "отпуск"})),
                      response(text_msg("Поиск недоступен.")))
    try:
        result = await stand.chat("ivanov", "Как оформить отпуск?")
    finally:
        monkeypatch.delenv("TOOL_TIMEOUT_S")
        reset_settings()
    assert _tool_outputs(stand.openai.requests[1])[0]["error"]["code"] == "unavailable"
    assert result["text"] == "Поиск недоступен."


async def test_langfuse_failure_does_not_change_answer(stand):
    set_tracer(Tracer(client=FakeLangfuse(fail=True)))
    stand.openai.push(response(fn_call("sd_list_ticket_types", {})), response(text_msg("Есть 5 типов.")))
    result = await stand.chat("ivanov", "Какие типы обращений есть?")
    assert result["text"] == "Есть 5 типов."
    assert result["trace_id"]
    fb = await stand.orchestrator.post("/feedback", json={"trace_id": result["trace_id"], "value": 1})
    assert fb.status_code == 200 and fb.json() == {"ok": True}


FRAGMENTS = {"items": [
    {"title": "Регламент оформления ежегодного отпуска", "section": "Порядок оформления",
     "url": "http://localhost:8040/ui/pages/hr-vacation", "updated_at": "2026-11-04T10:00:00+03:00",
     "text": "Оформите заявление за 14 дней."},
    {"title": "Порядок действий при болезни", "section": "Уведомление",
     "url": "http://localhost:8040/ui/pages/hr-sick-leave", "updated_at": "2026-10-04T10:00:00+03:00",
     "text": "Сообщите руководителю."},
]}


async def test_sources_are_built_by_code_from_cited_fragments(stand, monkeypatch):
    async def fake_search(token, query, top_k=5):
        return FRAGMENTS

    monkeypatch.setattr(search_client, "search", fake_search)
    stand.openai.push(response(fn_call("search_regulations", {"query": "отпуск"})),
                      response(text_msg("1. Оформите заявление за 14 дней [1].")))
    result = await stand.chat("ivanov", "Как оформить отпуск?")
    assert result["text"] == "1. Оформите заявление за 14 дней."
    assert result["sources"] == [{"title": "Регламент оформления ежегодного отпуска",
                                  "url": "http://localhost:8040/ui/pages/hr-vacation",
                                  "updated_at": "2026-11-04"}]
    # модели не передаются ссылки: только номер, заголовок, раздел, дата и текст
    [output] = _tool_outputs(stand.openai.requests[1])
    assert output["items"][0]["ref"] == 1 and "url" not in output["items"][0]


async def test_empty_search_replaced_with_not_found(stand, monkeypatch):
    async def empty_search(token, query, top_k=5):
        return {"items": [], "has_more": False}

    monkeypatch.setattr(search_client, "search", empty_search)
    stand.openai.push(response(fn_call("search_regulations", {"query": "парковка"})),
                      response(text_msg("Пропуск на парковку заказывается у охраны.")))
    result = await stand.chat("ivanov", "Как заказать пропуск на парковку?")
    assert result["text"] == prompts.REGULATION_NOT_FOUND
    assert result["sources"] == []


async def test_history_is_passed_and_token_never_reaches_model(stand):
    stand.openai.push(response(text_msg("Первый ответ.")), response(text_msg("Второй ответ.")))
    await stand.chat("ivanov", "Первый вопрос", session_id="hist")
    await stand.chat("ivanov", "Второй вопрос", session_id="hist")
    second = stand.openai.requests[1]["input"]
    texts = [i.get("content") for i in second if isinstance(i, dict) and "role" in i]
    assert texts == ["Первый вопрос", "Первый ответ.", "Второй вопрос"]
    from common.redis_client import get_redis

    token = await get_redis().get("token:hist")
    assert token and token not in stand.openai.all_request_text()


@pytest.mark.parametrize("employee_id", ["ivanov", "kuznetsova"])
async def test_prompt_contains_current_date_and_timezone(stand, employee_id):
    stand.openai.push(response(text_msg("Ок")))
    await stand.chat(employee_id, "Какое сегодня число?")
    instructions = stand.openai.requests[0]["instructions"]
    from common.settings import get_settings

    assert get_settings().today().isoformat() in instructions
    assert "Europe/Moscow" in instructions
    assert f"логин {employee_id}" in instructions
