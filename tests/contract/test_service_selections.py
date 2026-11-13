"""T085: сервисные выборки точки MCP /service — только для токена role: recs-service."""

from __future__ import annotations

from datetime import timedelta

import pytest
from common.settings import get_settings

from services.orchestrator.agent.registry import REGISTRY, model_tools
from tests.contract.helpers import error_code, mcp_call

SELECTIONS = ("billing_select_invoices", "crm_select_client_managers", "sd_select_stale_tickets",
              "calendar_select_events")


def _tomorrow() -> str:
    return (get_settings().today() + timedelta(days=1)).isoformat()


def _items(body: dict, keys: set[str]) -> list[dict]:
    assert "error" not in body, body
    assert isinstance(body["items"], list) and isinstance(body["has_more"], bool)
    assert len(body["items"]) <= 200
    for item in body["items"]:
        assert keys <= set(item), item
    return body["items"]


VALID = {
    "billing_select_invoices": {"status": "overdue"},
    "crm_select_client_managers": {"client_ids": ["C001", "C006"]},
    "sd_select_stale_tickets": {"idle_days": 1},
    "calendar_select_events": {"date": "2026-01-01"},
}


async def test_billing_select_invoices(stand, tokens):
    body = await mcp_call("service", "billing_select_invoices", {"status": "overdue"}, tokens["recs-service"])
    items = _items(body, {"number", "client_id", "due_date", "amount", "overdue_days", "url"})
    numbers = {i["number"] for i in items}
    assert "СЧ-2026-0142" in numbers and "СЧ-2026-0202" in numbers  # клиенты обоих менеджеров
    assert all(i["overdue_days"] > 0 for i in items)

    body = await mcp_call("service", "billing_select_invoices", {"status": "due_soon"}, tokens["recs-service"])
    numbers = {i["number"] for i in _items(body, {"number", "due_date"})}
    assert "СЧ-2026-0143" in numbers and "СЧ-2026-0142" not in numbers


async def test_crm_select_client_managers(stand, tokens):
    body = await mcp_call("service", "crm_select_client_managers", {"client_ids": ["C001", "C006"]},
                          tokens["recs-service"])
    items = {i["client_id"]: i for i in _items(body, {"client_id", "name", "manager"})}
    assert items["C001"]["manager"]["name"].startswith("Иванов")
    assert items["C006"]["manager"]["name"].startswith("Петрова")


async def test_sd_select_stale_tickets(stand, tokens):
    body = await mcp_call("service", "sd_select_stale_tickets", {"idle_days": 1, "due_within": 24},
                          tokens["recs-service"])
    items = _items(body, {"number", "title", "assignee", "status", "due_at", "url"})
    assert items
    assert all(i["status"] not in ("resolved", "closed") for i in items)


async def test_calendar_select_events(stand, tokens):
    body = await mcp_call("service", "calendar_select_events", {"date": _tomorrow(), "users": ["ivanov"]},
                          tokens["recs-service"])
    items = _items(body, {"user", "subject", "start", "organizer", "attendees"})
    assert items and {i["user"] for i in items} == {"ivanov"}
    assert "Разбор воронки продаж за месяц" in {i["subject"] for i in items}


@pytest.mark.parametrize("name,args", [
    ("billing_select_invoices", {"status": "paid"}),
    ("billing_select_invoices", {}),
    ("crm_select_client_managers", {"client_ids": [f"C{i:03}" for i in range(101)]}),
    ("crm_select_client_managers", {}),
    ("sd_select_stale_tickets", {"idle_days": 0}),
    ("sd_select_stale_tickets", {"idle_days": 2, "extra": True}),
    ("calendar_select_events", {"date": "завтра"}),
    ("calendar_select_events", {"users": ["ivanov"]}),
])
async def test_invalid_input(stand, tokens, name, args):
    assert error_code(await mcp_call("service", name, args, tokens["recs-service"])) == "invalid_input"


@pytest.mark.parametrize("name", SELECTIONS)
async def test_employee_and_missing_token_forbidden(stand, tokens, expired_token, name):
    for token in (tokens["ivanov"], tokens["kuznetsova"], tokens["indexer"], expired_token, None):
        body = await mcp_call("service", name, VALID[name], token)
        assert error_code(body) == "forbidden", (token is None, body)


async def test_mock_select_routes_forbid_employee(stand, tokens):
    headers = {"Authorization": f"Bearer {tokens['kuznetsova']}"}
    for method, path, kw in (
        ("GET", "/billing/invoices/select", {"params": {"status": "overdue"}}),
        ("POST", "/crm/clients/managers", {"json": {"client_ids": ["C001"]}}),
        ("GET", "/sd/tickets/select", {"params": {"idle_days": 1}}),
        ("GET", "/calendar/events/select", {"params": {"date": _tomorrow()}}),
    ):
        resp = await stand.mock.request(method, path, headers=headers, **kw)
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "forbidden"


def test_selections_not_in_agent_registry():
    names = set(REGISTRY) | {t["name"] for t in model_tools()}
    assert not names & set(SELECTIONS)
