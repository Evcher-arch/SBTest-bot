"""История 2 — статус заявки или счёта: приёмочные сценарии 1–7 (T060).

Модель подменена: она «вызывает» инструменты по сценарию, а тест проверяет, что код
исполнил вызов с правами спрашивающего и вернул модели корректный результат.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from common.settings import get_settings

from tests.conftest import bearer
from tests.support import fn_call, response, text_msg

pytestmark = pytest.mark.integration

PETROVA_INVOICE = "СЧ-2026-0201"  # клиент C006 «Альфа-Строй», ответственный — Петрова


def tool_outputs(stand, request_index: int | None = None) -> list[dict]:
    """Результаты инструментов, переданные модели в запросе (по умолчанию — в последнем)."""
    request = stand.openai.requests[-1 if request_index is None else request_index]
    return [json.loads(i["output"]) for i in request["input"]
            if isinstance(i, dict) and i.get("type") == "function_call_output"]


async def test_s1_client_invoices_list(stand):
    stand.openai.push(
        response(fn_call("crm_find_client", {"name": "Ромашка"})),
        response(fn_call("billing_list_invoices", {"client_id": "C001"})),
        response(text_msg("Счета ООО «Ромашка»: …")),
    )
    result = await stand.chat("ivanov", "Какие счета у Ромашки?")
    assert result["text"] == "Счета ООО «Ромашка»: …"

    clients, invoices = tool_outputs(stand)
    assert [c["client_id"] for c in clients["items"]] == ["C001"]
    assert clients["items"][0]["url"].endswith("/ui/clients/C001")
    assert invoices["has_more"] is False
    numbers = {i["number"] for i in invoices["items"]}
    assert {"СЧ-2026-0142", "СЧ-2026-0143", "СЧ-2026-0131", "СЧ-2026-0137"} <= numbers
    for item in invoices["items"]:
        assert {"number", "date", "due_date", "amount", "status", "overdue_days", "url"} <= set(item)
        assert "/ui/invoices/" in item["url"]
    overdue = next(i for i in invoices["items"] if i["number"] == "СЧ-2026-0142")
    assert overdue["status"] == "overdue"
    assert overdue["overdue_days"] == 5
    assert overdue["amount"] == 184500.0


async def test_s2_foreign_invoice_is_forbidden_and_differs_from_not_found(stand):
    stand.openai.push(
        response(fn_call("billing_get_invoice", {"number": PETROVA_INVOICE}),
                 fn_call("billing_get_invoice", {"number": "СЧ-2026-9999"})),
        response(text_msg("Нет доступа к счёту.")),
    )
    await stand.chat("ivanov", f"Что со счётом {PETROVA_INVOICE}?")
    forbidden, missing = tool_outputs(stand)
    assert forbidden == {"error": {"code": "forbidden", "message": forbidden["error"]["message"]}}
    assert missing["error"]["code"] == "not_found"
    # данные чужого счёта не попали к модели
    assert "Альфа-Строй" not in stand.openai.all_request_text()

    # клиент Петровой не виден Иванову и в поиске
    stand.openai.push(response(fn_call("crm_find_client", {"name": "Альфа"})), response(text_msg("—")))
    await stand.chat("ivanov", "Найди клиента Альфа")
    [found] = tool_outputs(stand)
    assert found["items"] == []


async def test_s3_manager_sees_subordinate_invoice(stand):
    stand.openai.push(
        response(fn_call("billing_get_invoice", {"number": PETROVA_INVOICE})),
        response(text_msg("Данные счёта.")),
    )
    await stand.chat("kuznetsova", f"Что со счётом {PETROVA_INVOICE}?")
    [invoice] = tool_outputs(stand)
    assert invoice["number"] == PETROVA_INVOICE
    assert invoice["client_id"] == "C006"
    assert isinstance(invoice["payments"], list)


async def test_s4_my_tickets(stand):
    stand.openai.push(
        response(fn_call("sd_list_my_tickets", {"role": "author"})),
        response(text_msg("Ваши обращения: …")),
    )
    await stand.chat("ivanov", "Что с моими заявками?")
    [tickets] = tool_outputs(stand)
    numbers = {t["number"] for t in tickets["items"]}
    assert {"SD-1040", "SD-1041", "SD-1044"} <= numbers
    assert not numbers & {"SD-1046", "SD-1050"}  # обращения Петровой и Кузнецовой
    for t in tickets["items"]:
        assert {"number", "type", "title", "status", "due_at", "url"} <= set(t)


async def test_s5_manager_sees_subordinate_tickets_peer_does_not(stand):
    stand.openai.push(
        response(fn_call("sd_list_my_tickets", {"role": "author", "employee": "ivanov"})),
        response(text_msg("Обращения Иванова: …")),
    )
    await stand.chat("kuznetsova", "Какие обращения у Иванова?")
    [tickets] = tool_outputs(stand)
    assert "SD-1040" in {t["number"] for t in tickets["items"]}

    stand.openai.push(
        response(fn_call("sd_list_my_tickets", {"role": "author", "employee": "ivanov"})),
        response(text_msg("Нет доступа.")),
    )
    await stand.chat("petrova", "Какие обращения у Иванова?")
    [denied] = tool_outputs(stand)
    assert denied["error"]["code"] == "forbidden"
    assert "SD-1040" not in json.dumps(stand.openai.requests[-1], ensure_ascii=False)


async def test_s6_only_own_calendar_events(stand):
    s = get_settings()
    tomorrow = s.now().date() + timedelta(days=1)
    start = f"{tomorrow.isoformat()}T00:00:00+03:00"
    end = f"{tomorrow.isoformat()}T23:59:59+03:00"
    stand.openai.push(
        response(fn_call("calendar_list_my_events", {"from": start, "to": end})),
        response(text_msg("Завтра у вас: …")),
    )
    await stand.chat("ivanov", "Какие у меня встречи завтра?")
    [events] = tool_outputs(stand)
    subjects = {e["subject"] for e in events["items"]}
    assert "Разбор воронки продаж за месяц" in subjects
    # встреча Петровой с клиентом и планёрка Service-Desk не показаны
    assert "Переговоры по новому договору поставки" not in subjects
    assert "Планёрка Service-Desk" not in subjects
    for e in events["items"]:
        people = {a.get("employee_id") for a in e["attendees"]} | {e["organizer"]["id"]}
        assert "ivanov" in people


async def test_s7_one_system_down_other_answers(stand):
    resp = await stand.mock.post("/admin/outage", json={"system": "billing", "enabled": True})
    assert resp.status_code == 200
    stand.openai.push(
        response(fn_call("billing_list_invoices", {"client_id": "C001"}),
                 fn_call("sd_list_my_tickets", {"role": "author"})),
        response(text_msg("Биллинг недоступен; ваши обращения: …")),
    )
    result = await stand.chat("ivanov", "Что со счетами Ромашки и с моими заявками?")
    billing, tickets = tool_outputs(stand)
    assert billing["error"]["code"] == "unavailable"
    assert "Биллинг" in billing["error"]["message"]
    assert tickets["items"], "обращения должны вернуться при недоступном биллинге"
    assert result["text"] == "Биллинг недоступен; ваши обращения: …"
    # промпт требует назвать недоступную систему и ответить на остальное
    assert "временно недоступна" in stand.openai.requests[0]["instructions"]


async def test_long_list_is_truncated_with_has_more(stand, tokens):
    headers = bearer(tokens["ivanov"])
    for n in range(21):
        resp = await stand.mock.post("/sd/tickets", json={
            "type_id": "other", "title": f"Тестовое обращение {n}", "description": "Для проверки списка",
        }, headers=headers | {"Idempotency-Key": f"long-list-{n}"})
        assert resp.status_code == 201, resp.text

    stand.openai.push(
        response(fn_call("sd_list_my_tickets", {"role": "author"})),
        response(text_msg("Показаны не все обращения.")),
    )
    await stand.chat("ivanov", "Покажи все мои заявки")
    [tickets] = tool_outputs(stand)
    assert len(tickets["items"]) == 20
    assert tickets["has_more"] is True
    assert "показаны не все" in stand.openai.requests[0]["instructions"]
