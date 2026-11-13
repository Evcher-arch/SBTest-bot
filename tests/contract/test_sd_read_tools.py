"""T058: sd_list_my_tickets и sd_get_ticket — схемы, фильтр employee, чужой доступ."""

from __future__ import annotations

import pytest

from tests.contract.helpers import assert_list, error_code, mcp_call

LIST_KEYS = {"number", "type", "title", "status", "due_at", "url"}


async def test_list_author_schema(stand, tokens):
    body = await mcp_call("sd", "sd_list_my_tickets", {"role": "author"}, tokens["ivanov"])
    items = assert_list(body, LIST_KEYS)
    numbers = {i["number"] for i in items}
    assert {"SD-1040", "SD-1041"} <= numbers
    assert not numbers & {"SD-1046", "SD-1050"}
    assert all("/ui/tickets/" in i["url"] for i in items)


async def test_list_assignee_and_status(stand, tokens):
    body = await mcp_call("sd", "sd_list_my_tickets", {"role": "assignee", "status": "in_progress"},
                          tokens["sidorov"])
    items = assert_list(body, LIST_KEYS)
    assert items and all(i["status"] == "in_progress" for i in items)


async def test_get_ticket_with_history(stand, tokens):
    body = await mcp_call("sd", "sd_get_ticket", {"number": "SD-1040"}, tokens["ivanov"])
    assert "error" not in body, body
    assert LIST_KEYS | {"assignee", "history"} <= set(body)
    assert body["assignee"]["name"].startswith("Сидоров")
    assert len(body["history"]) >= 2
    for entry in body["history"]:
        assert {"at", "actor", "action"} <= set(entry)


@pytest.mark.parametrize("name,args", [
    ("sd_list_my_tickets", {"role": "watcher"}),
    ("sd_list_my_tickets", {}),
    ("sd_list_my_tickets", {"role": "author", "status": "done"}),
    ("sd_list_my_tickets", {"role": "author", "as_user": "petrova"}),
    ("sd_get_ticket", {}),
])
async def test_invalid_input(stand, tokens, name, args):
    assert error_code(await mcp_call("sd", name, args, tokens["ivanov"])) == "invalid_input"


async def test_no_token_and_expired_forbidden(stand, expired_token):
    for token in (None, expired_token):
        assert error_code(await mcp_call("sd", "sd_list_my_tickets", {"role": "author"}, token)) == "forbidden"
        assert error_code(await mcp_call("sd", "sd_get_ticket", {"number": "SD-1040"}, token)) == "forbidden"


async def test_employee_filter_is_not_identity_change(stand, tokens):
    """Руководитель видит обращения подчинённого; коллега и подчинённый — forbidden."""
    body = await mcp_call("sd", "sd_list_my_tickets", {"role": "author", "employee": "ivanov"},
                          tokens["kuznetsova"])
    numbers = {i["number"] for i in assert_list(body, LIST_KEYS)}
    assert "SD-1040" in numbers and "SD-1050" not in numbers

    body = await mcp_call("sd", "sd_list_my_tickets", {"role": "author", "employee": "ivanov"},
                          tokens["petrova"])
    assert error_code(body) == "forbidden"
    body = await mcp_call("sd", "sd_list_my_tickets", {"role": "author", "employee": "kuznetsova"},
                          tokens["ivanov"])
    assert error_code(body) == "forbidden"


async def test_foreign_ticket(stand, tokens):
    """Чужое обращение → forbidden, несуществующее → not_found, руководитель автора → данные."""
    body = await mcp_call("sd", "sd_get_ticket", {"number": "SD-1046"}, tokens["ivanov"])
    assert error_code(body) == "forbidden"
    body = await mcp_call("sd", "sd_get_ticket", {"number": "SD-9999"}, tokens["ivanov"])
    assert error_code(body) == "not_found"
    body = await mcp_call("sd", "sd_get_ticket", {"number": "SD-1046"}, tokens["kuznetsova"])
    assert "error" not in body and body["number"] == "SD-1046"
    body = await mcp_call("sd", "sd_get_ticket", {"number": "SD-1046"}, tokens["sidorov"])
    assert "error" not in body
