"""T086: recs_list_my (GET /signals) и recs_set_subscription (PUT /subscriptions/{rule})."""

from __future__ import annotations

from datetime import timedelta

import pytest
from common.settings import get_settings

from services.recommender import db as recs_db
from tests.conftest import bearer
from tests.contract.helpers import assert_rest_error

pytestmark = pytest.mark.integration

RULES = {"invoice_overdue", "invoice_due_soon", "meeting_tomorrow", "ticket_stale"}


async def _signals(stand, token: str) -> list[dict]:
    resp = await stand.recommender.get("/signals", headers=bearer(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["has_more"], bool) and len(body["items"]) <= 20
    for item in body["items"]:
        assert {"rule", "text", "object", "url"} <= set(item)
        assert item["rule"] in RULES and item["text"]
    return body["items"]


async def test_signals_schema_and_only_own(full_stand, tokens):
    ivanov = await _signals(full_stand, tokens["ivanov"])
    petrova = await _signals(full_stand, tokens["petrova"])
    assert any("СЧ-2026-0142" in i["text"] for i in ivanov)
    assert not any("СЧ-2026-02" in i["text"] for i in ivanov)  # счета клиентов Петровой
    assert any("СЧ-2026-0202" in i["text"] for i in petrova)
    assert not any("СЧ-2026-0142" in i["text"] for i in petrova)


async def test_signals_reflect_current_data_without_writing(full_stand, tokens):
    before_rows = await recs_db.count_signals()
    assert any("СЧ-2026-0142" in i["text"] for i in await _signals(full_stand, tokens["ivanov"]))

    resp = await full_stand.mock.patch("/admin/invoices/СЧ-2026-0142", json={"status": "paid"})
    assert resp.status_code == 200
    assert not any("СЧ-2026-0142" in i["text"] for i in await _signals(full_stand, tokens["ivanov"]))

    # счёт без сигнала становится просроченным — сигнал появляется без POST /admin/run
    past = (get_settings().today() - timedelta(days=4)).isoformat()
    resp = await full_stand.mock.patch("/admin/invoices/СЧ-2026-0151", json={"due_date": past})
    assert resp.status_code == 200
    assert any("СЧ-2026-0151" in i["text"] for i in await _signals(full_stand, tokens["ivanov"]))

    assert await recs_db.count_signals() == before_rows == 0
    assert full_stand.telegram.sent() == []


async def test_set_subscription_schema_and_effect(full_stand, tokens):
    resp = await full_stand.recommender.put("/subscriptions/invoice_overdue", json={"enabled": False},
                                            headers=bearer(tokens["ivanov"]))
    assert resp.status_code == 200, resp.text
    items = {i["rule"]: i["enabled"] for i in resp.json()["items"]}
    assert set(items) == RULES
    assert items["invoice_overdue"] is False
    assert all(items[r] for r in RULES - {"invoice_overdue"})

    ivanov = await _signals(full_stand, tokens["ivanov"])
    assert not any(i["rule"] == "invoice_overdue" for i in ivanov)
    assert any(i["rule"] == "invoice_due_soon" for i in ivanov)
    # подписка Иванова не влияет на Петрову
    assert any(i["rule"] == "invoice_overdue" for i in await _signals(full_stand, tokens["petrova"]))

    resp = await full_stand.recommender.put("/subscriptions/invoice_overdue", json={"enabled": True},
                                            headers=bearer(tokens["ivanov"]))
    assert {i["rule"]: i["enabled"] for i in resp.json()["items"]}["invoice_overdue"] is True
    assert any(i["rule"] == "invoice_overdue" for i in await _signals(full_stand, tokens["ivanov"]))


@pytest.mark.parametrize("rule,body", [
    ("no_such_rule", {"enabled": False}),
    ("invoice_overdue", {"enabled": False, "employee": "petrova"}),
    ("invoice_overdue", {}),
])
async def test_invalid_input(full_stand, tokens, rule, body):
    resp = await full_stand.recommender.put(f"/subscriptions/{rule}", json=body,
                                            headers=bearer(tokens["ivanov"]))
    assert_rest_error(resp, 400, "invalid_input")


async def test_invalid_date(full_stand, tokens):
    resp = await full_stand.recommender.get("/signals", params={"date": "завтра"},
                                            headers=bearer(tokens["ivanov"]))
    assert_rest_error(resp, 400, "invalid_input")


async def test_no_token_and_expired_forbidden(full_stand, tokens, expired_token):
    for headers in ({}, bearer(expired_token), bearer(tokens["recs-service"])):
        assert_rest_error(await full_stand.recommender.get("/signals", headers=headers), (401, 403),
                          "forbidden")
        resp = await full_stand.recommender.put("/subscriptions/invoice_overdue", json={"enabled": False},
                                                headers=headers)
        assert_rest_error(resp, (401, 403), "forbidden")
