"""Состав стенда: реестр инструментов агента, единственный изменяющий инструмент,
отсутствие прямых обращений оркестратора к заглушке (принцип V), отсутствие параметра
«от чьего имени» в схемах."""

from __future__ import annotations

import re
from pathlib import Path

from common.tool_schemas import AGENT_TOOLS, SERVICE_SELECTIONS, model_schema

from services.mcp_systems.main import BUILDERS
from services.orchestrator.agent import registry

ROOT = Path(__file__).resolve().parents[2]

CONTRACT_TOOLS = {
    "search_regulations", "crm_find_client", "billing_list_invoices", "billing_get_invoice",
    "sd_list_my_tickets", "sd_get_ticket", "sd_list_ticket_types", "sd_create_ticket",
    "calendar_list_my_events", "recs_list_my", "recs_set_subscription",
}
CONTRACT_SELECTIONS = {
    "billing_select_invoices", "crm_select_client_managers", "sd_select_stale_tickets",
    "calendar_select_events",
}
IDENTITY_PARAMS = {"employee_id", "user", "user_id", "as_user", "on_behalf_of", "sub", "login",
                   "token", "authorization", "author", "author_id"}


def test_registry_has_exactly_the_eleven_contract_tools():
    assert set(registry.REGISTRY) == CONTRACT_TOOLS
    assert len(registry.REGISTRY) == 11
    assert {t["name"] for t in registry.model_tools()} == CONTRACT_TOOLS


def test_no_service_selection_in_agent_registry():
    assert set(SERVICE_SELECTIONS) == CONTRACT_SELECTIONS
    assert not CONTRACT_SELECTIONS & set(registry.REGISTRY)
    assert not CONTRACT_SELECTIONS & {t["name"] for t in registry.model_tools()}
    for name in CONTRACT_SELECTIONS:
        assert not registry.is_allowed(name)


def test_only_mutating_tool_is_sd_create_ticket():
    writers = {n for n, s in registry.REGISTRY.items() if s.kind == "write" or s.intercept}
    assert writers == {"sd_create_ticket"}
    assert registry.REGISTRY["sd_create_ticket"].intercept is True


def test_schemas_have_no_identity_parameter():
    for name in CONTRACT_TOOLS:
        schema = AGENT_TOOLS[name]["schema"]
        assert schema["additionalProperties"] is False, name
        assert not IDENTITY_PARAMS & {p.lower() for p in schema["properties"]}, name
        assert not IDENTITY_PARAMS & set(model_schema(name)["properties"]), name
    for name in CONTRACT_SELECTIONS:
        schema = SERVICE_SELECTIONS[name]["schema"]
        assert schema["additionalProperties"] is False, name
        assert not IDENTITY_PARAMS & {p.lower() for p in schema["properties"]}, name
    # sd_list_my_tickets.employee — фильтр по подчинённому, а не смена личности (contracts/agent-tools.md)
    assert "employee" in AGENT_TOOLS["sd_list_my_tickets"]["schema"]["properties"]
    assert "idempotency_key" not in model_schema("sd_create_ticket")["properties"]


def test_orchestrator_does_not_call_mock_systems_directly():
    pattern = re.compile(r"MOCK_URL|mock_url|mock-systems:8040|:8040")
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "services" / "orchestrator").rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_orchestrator_does_not_connect_to_service_endpoint():
    from services.orchestrator.clients import mcp_client

    assert set(mcp_client.SERVERS) == {"crm", "billing", "sd", "calendar"}
    sources = "\n".join(p.read_text(encoding="utf-8")
                        for p in (ROOT / "services" / "orchestrator").rglob("*.py"))
    assert "/service/mcp" not in sources
    assert not any(name in sources for name in CONTRACT_SELECTIONS)


async def test_mcp_endpoints_expose_contract_tools():
    expected = {
        "crm": {"crm_find_client"},
        "billing": {"billing_list_invoices", "billing_get_invoice"},
        "sd": {"sd_list_my_tickets", "sd_get_ticket", "sd_list_ticket_types", "sd_create_ticket"},
        "calendar": {"calendar_list_my_events"},
        "service": CONTRACT_SELECTIONS,
    }
    for endpoint, build in BUILDERS.items():
        server = build()
        tools = await server.list_tools()
        assert {t.name for t in tools} == expected[endpoint], endpoint
