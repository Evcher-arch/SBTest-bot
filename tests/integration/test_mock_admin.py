"""Пульт ведущего заглушки: сброс данных, даты от текущего дня, имитация отказа (T025)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from common.settings import get_settings

from tests.conftest import bearer

pytestmark = pytest.mark.integration

INVOICE = "СЧ-2026-0142"  # ООО «Ромашка», срок оплаты — 5 дней назад


def _today() -> date:
    return get_settings().now().date()


async def _invoice(stand, tokens) -> dict:
    resp = await stand.mock.get(f"/billing/invoices/{INVOICE}", headers=bearer(tokens["ivanov"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_reset_restores_seed_data(stand, tokens):
    before = await _invoice(stand, tokens)
    state_before = (await stand.mock.get("/admin/state")).json()

    resp = await stand.mock.patch(f"/admin/invoices/{INVOICE}", json={
        "due_date": (_today() + timedelta(days=30)).isoformat(),
        "status": "partially_paid",
        "add_payment": {"date": _today().isoformat(), "amount": 1000},
    })
    assert resp.status_code == 200, resp.text
    resp = await stand.mock.post("/sd/tickets", json={
        "type_id": "other", "title": "Временное обращение", "description": "Будет удалено сбросом",
    }, headers=bearer(tokens["ivanov"]) | {"Idempotency-Key": "admin-test-1"})
    assert resp.status_code == 201, resp.text

    changed = await _invoice(stand, tokens)
    assert changed["due_date"] != before["due_date"]
    assert changed["payments"] != before["payments"]
    assert (await stand.mock.get("/admin/state")).json()["counts"]["ticket"] == \
        state_before["counts"]["ticket"] + 1

    resp = await stand.mock.post("/admin/reset")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mock_systems"]["ok"] is True
    assert "mock_systems" in body["reset"]
    # оркестратор и бот подняты в стенде — их сброс выполнен
    assert {"orchestrator", "bot"} <= set(body["reset"])

    after = await _invoice(stand, tokens)
    assert after == before
    assert (await stand.mock.get("/admin/state")).json()["counts"] == state_before["counts"]


async def test_reset_reports_unavailable_services_without_failing(stand):
    stand.router.down.add("recommender")
    resp = await stand.mock.post("/admin/reset")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "recommender" in body["failed"]
    assert body["recommender"]["ok"] is False
    assert "mock_systems" in body["reset"]


async def test_dates_are_relative_to_today(stand, tokens):
    invoice = await _invoice(stand, tokens)
    assert invoice["due_date"] == (_today() - timedelta(days=5)).isoformat()
    assert invoice["date"] == (_today() - timedelta(days=35)).isoformat()
    assert invoice["overdue_days"] == 5
    assert invoice["status"] == "overdue"

    resp = await stand.mock.get("/sd/tickets/SD-1040", headers=bearer(tokens["ivanov"]))
    ticket = resp.json()
    assert ticket["created_at"].startswith((_today() - timedelta(days=3)).isoformat())
    assert ticket["due_at"].startswith((_today() + timedelta(days=1)).isoformat())


async def test_outage_toggles_system_availability(stand, tokens):
    headers = bearer(tokens["ivanov"])
    resp = await stand.mock.post("/admin/outage", json={"system": "billing", "enabled": True})
    assert resp.status_code == 200
    assert resp.json()["outages"] == ["billing"]

    resp = await stand.mock.get(f"/billing/invoices/{INVOICE}", headers=headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "unavailable"
    # остальные системы работают
    resp = await stand.mock.get("/sd/tickets", params={"role": "author"}, headers=headers)
    assert resp.status_code == 200

    state = (await stand.mock.get("/admin/state")).json()
    assert state["outages"] == ["billing"]

    resp = await stand.mock.post("/admin/outage", json={"system": "billing", "enabled": False})
    assert resp.json()["outages"] == []
    resp = await stand.mock.get(f"/billing/invoices/{INVOICE}", headers=headers)
    assert resp.status_code == 200


async def test_reset_clears_outages(stand):
    await stand.mock.post("/admin/outage", json={"system": "sd", "enabled": True})
    await stand.mock.post("/admin/reset")
    assert (await stand.mock.get("/admin/state")).json()["outages"] == []
