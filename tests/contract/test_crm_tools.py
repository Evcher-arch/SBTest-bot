"""T056: crm_find_client — схема ответа, проверка входа, токен, чужой доступ."""

from __future__ import annotations

import pytest

from tests.conftest import bearer
from tests.contract.helpers import assert_list, assert_rest_error, error_code, mcp_call

ITEM_KEYS = {"client_id", "name", "manager", "url"}


async def test_find_by_name_schema(stand, tokens):
    body = await mcp_call("crm", "crm_find_client", {"name": "Ромашка"}, tokens["ivanov"])
    [item] = assert_list(body, ITEM_KEYS)
    assert item["client_id"] == "C001"
    assert "Ромашка" in item["name"]
    assert item["url"].endswith("/ui/clients/C001")
    assert isinstance(item["manager"], dict) and item["manager"]["name"].startswith("Иванов")


async def test_find_by_inn(stand, tokens):
    body = await mcp_call("crm", "crm_find_client", {"inn": "7701234567"}, tokens["ivanov"])
    assert [i["client_id"] for i in assert_list(body, ITEM_KEYS)] == ["C001"]


@pytest.mark.parametrize("name", ["ООО Ромашка", "ооо «ромашка»", "\"Ромашка\""])
async def test_find_by_name_ignores_quotes_and_case(stand, tokens, name):
    body = await mcp_call("crm", "crm_find_client", {"name": name}, tokens["ivanov"])
    assert [i["client_id"] for i in assert_list(body, ITEM_KEYS)] == ["C001"]


async def test_similar_names_return_several(stand, tokens):
    body = await mcp_call("crm", "crm_find_client", {"name": "Северный ветер"}, tokens["ivanov"])
    assert {i["client_id"] for i in assert_list(body, ITEM_KEYS)} == {"C002", "C003"}


@pytest.mark.parametrize("args", [
    {},
    {"name": "Ромашка", "inn": "7701234567"},
    {"name": "Р"},
    {"inn": "123"},
    {"name": "Ромашка", "employee": "petrova"},
])
async def test_invalid_input(stand, tokens, args):
    body = await mcp_call("crm", "crm_find_client", args, tokens["ivanov"])
    assert error_code(body) == "invalid_input"


async def test_rest_invalid_input(stand, tokens):
    for params in ({}, {"name": "Ромашка", "inn": "7701234567"}, {"name": "Р"}):
        resp = await stand.mock.get("/crm/clients", params=params, headers=bearer(tokens["ivanov"]))
        assert_rest_error(resp, 400, "invalid_input")


async def test_no_token_and_expired_forbidden(stand, expired_token):
    assert error_code(await mcp_call("crm", "crm_find_client", {"name": "Ромашка"}, None)) == "forbidden"
    body = await mcp_call("crm", "crm_find_client", {"name": "Ромашка"}, expired_token)
    assert error_code(body) == "forbidden"


async def test_foreign_client_not_visible(stand, tokens):
    """Чужой доступ: клиент Петровой не виден Иванову, карточка даёт forbidden."""
    body = await mcp_call("crm", "crm_find_client", {"name": "Альфа-Строй"}, tokens["ivanov"])
    assert assert_list(body, ITEM_KEYS) == []
    body = await mcp_call("crm", "crm_find_client", {"inn": "7806789012"}, tokens["ivanov"])
    assert assert_list(body, ITEM_KEYS) == []

    resp = await stand.mock.get("/crm/clients/C006", headers=bearer(tokens["ivanov"]))
    assert_rest_error(resp, 403, "forbidden")
    resp = await stand.mock.get("/crm/clients/C999", headers=bearer(tokens["ivanov"]))
    assert_rest_error(resp, 404, "not_found")


async def test_manager_sees_subordinates_clients(stand, tokens):
    body = await mcp_call("crm", "crm_find_client", {"name": "Альфа-Строй"}, tokens["petrova"])
    assert [i["client_id"] for i in assert_list(body, ITEM_KEYS)] == ["C006"]
    body = await mcp_call("crm", "crm_find_client", {"name": "Альфа-Строй"}, tokens["kuznetsova"])
    assert [i["client_id"] for i in assert_list(body, ITEM_KEYS)] == ["C006"]
    resp = await stand.mock.get("/crm/clients/C006", headers=bearer(tokens["kuznetsova"]))
    assert resp.status_code == 200
