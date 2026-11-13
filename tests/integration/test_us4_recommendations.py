"""История 4 — рекомендации: приёмочные сценарии 1–6 (spec.md).

Сервис рекомендаций работает на PostgreSQL; уведомления доставляет бот (POST /notify)
в чаты, где выбрана роль сотрудника. Telegram подменён и записывает отправленные сообщения.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import unquote

import pytest
from common.settings import get_settings

from services.recommender import db as recs_db
from tests.conftest import bearer, feed, tg_callback

pytestmark = pytest.mark.integration

CHATS = {"ivanov": 601, "petrova": 602, "sidorov": 603, "kuznetsova": 604}


async def choose_roles(stand, *employees: str) -> None:
    for emp in employees:
        await feed(stand, tg_callback(f"role:{emp}", user_id=CHATS[emp]))


def notes(stand, employee: str) -> list[str]:
    return [m.text for m in stand.telegram.sent()
            if int(m.chat_id) == CHATS[employee] and m.text.startswith("🔔")]


async def run_rules(stand) -> dict:
    resp = await stand.recommender.post("/admin/run")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_overdue_invoice_notifies_manager_only_once(full_stand, tokens):
    stand = full_stand
    await choose_roles(stand, "ivanov", "petrova")
    await run_rules(stand)  # исходные сигналы стенда
    stand.telegram.calls.clear()

    # Сценарий 1: счёт клиента Иванова стал просроченным
    due = (get_settings().today() - timedelta(days=4)).isoformat()
    resp = await stand.mock.patch("/admin/invoices/СЧ-2026-0160", json={"due_date": due})
    assert resp.status_code == 200, resp.text
    result = await run_rules(stand)
    assert result["signals_created"] == 1 and result["notifications_sent"] == 1
    [text] = notes(stand, "ivanov")
    assert "СЧ-2026-0160" in text
    assert "Вектор" in text  # название клиента
    assert "4 дн" in text  # дни просрочки
    assert "₽" in text  # сумма
    assert "/ui/invoices/СЧ-2026-0160" in unquote(text)  # ссылка
    assert notes(stand, "petrova") == []

    # Сценарий 2: повторная проверка ничего не отправляет
    stand.telegram.calls.clear()
    assert await run_rules(stand) == {"signals_created": 0, "notifications_sent": 0}
    assert notes(stand, "ivanov") == [] and notes(stand, "petrova") == []


async def test_stale_ticket_and_tomorrow_meeting(full_stand):
    stand = full_stand
    await choose_roles(stand, "ivanov", "petrova", "sidorov")
    await run_rules(stand)

    # Сценарий 3: обращение без движения дольше порога (SD-1047 — 6 дней) → исполнителю
    sidorov = "\n".join(notes(stand, "sidorov"))
    assert "SD-1047" in sidorov
    assert "SD-1047" not in "\n".join(notes(stand, "ivanov"))

    # Сценарий 4: завтрашняя встреча с руководителем (Иванов) и с клиентом (Петрова)
    ivanov = notes(stand, "ivanov")
    assert len(ivanov) == 1, "несколько сигналов получателя — одним сообщением"
    assert "Разбор воронки продаж за месяц" in ivanov[0]
    assert "СЧ-2026-0142" in ivanov[0]  # там же просроченный счёт
    assert "Переговоры по новому договору поставки" in "\n".join(notes(stand, "petrova"))
    # встреча без руководителя и клиента не напоминается
    assert "Обмен клиентами между менеджерами" not in "\n".join(notes(stand, "petrova") + ivanov)


async def test_digest_before_run_does_not_block_notification(full_stand, tokens):
    stand = full_stand
    await choose_roles(stand, "ivanov")

    # Сценарий 5: сводка по запросу показывает актуальные сигналы и ничего не записывает
    resp = await stand.recommender.get("/signals", headers=bearer(tokens["ivanov"]))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert any(i["rule"] == "invoice_overdue" and "СЧ-2026-0142" in i["text"] for i in items)
    assert all(set(i) >= {"rule", "text", "object", "url"} for i in items)
    assert await recs_db.count_signals() == 0
    assert notes(stand, "ivanov") == []

    await run_rules(stand)
    assert "СЧ-2026-0142" in "\n".join(notes(stand, "ivanov"))


async def test_disabled_rule_affects_only_that_rule(full_stand, tokens):
    stand = full_stand
    await choose_roles(stand, "ivanov", "petrova")

    # Сценарий 6: Иванов отключает правило о сроках оплаты
    resp = await stand.recommender.put("/subscriptions/invoice_due_soon", json={"enabled": False},
                                       headers=bearer(tokens["ivanov"]))
    assert resp.status_code == 200, resp.text
    subs = {i["rule"]: i["enabled"] for i in resp.json()["items"]}
    assert subs["invoice_due_soon"] is False and subs["invoice_overdue"] is True

    signals = (await stand.recommender.get("/signals", headers=bearer(tokens["ivanov"]))).json()["items"]
    assert not any(i["rule"] == "invoice_due_soon" for i in signals)

    await run_rules(stand)
    ivanov = "\n".join(notes(stand, "ivanov"))
    assert "СЧ-2026-0143" not in ivanov  # срок через 2 дня — правило отключено
    assert "СЧ-2026-0142" in ivanov  # просрочка — правило работает
    assert "СЧ-2026-0210" in "\n".join(notes(stand, "petrova"))  # у Петровой правило включено


async def test_formulate_failure_sends_template_text(full_stand):
    stand = full_stand
    await choose_roles(stand, "ivanov")
    stand.router.down.add("orchestrator")
    try:
        result = await run_rules(stand)
    finally:
        stand.router.down.discard("orchestrator")
    assert result["notifications_sent"] >= 1
    [text] = notes(stand, "ivanov")
    assert text.startswith("🔔 Иван, важное на сегодня:")
    assert "СЧ-2026-0142" in text


async def test_model_text_with_wrong_facts_is_replaced_by_template(full_stand):
    from tests.support import response, text_msg

    stand = full_stand
    await choose_roles(stand, "ivanov")
    stand.openai.push(response(text_msg("Счёт СЧ-2026-0142 просрочен на 999 дней.")))
    await run_rules(stand)
    [text] = notes(stand, "ivanov")
    assert "999" not in text
    assert text.startswith("🔔 Иван, важное на сегодня:")


async def test_stand_reset_clears_signals_and_subscriptions(full_stand, tokens):
    stand = full_stand
    await choose_roles(stand, "ivanov")
    await stand.recommender.put("/subscriptions/ticket_stale", json={"enabled": False},
                                headers=bearer(tokens["ivanov"]))
    await run_rules(stand)
    assert await recs_db.count_signals() > 0

    resp = await stand.mock.post("/admin/reset")
    assert resp.status_code == 200, resp.text
    assert "recommender" in resp.json()["reset"]
    assert await recs_db.count_signals() == 0
    subs = await recs_db.subscriptions("ivanov", ["ticket_stale"])
    assert subs == [{"rule": "ticket_stale", "enabled": True}]
    assert await recs_db.disabled_rules() == {}
