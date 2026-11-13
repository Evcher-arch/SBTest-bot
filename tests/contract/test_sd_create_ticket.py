"""T075: sd_list_ticket_types и sd_create_ticket — схемы, обязательные поля, ключ идемпотентности."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import bearer
from tests.contract.helpers import assert_list, assert_rest_error, error_code, mcp_call

VPN = {"type_id": "vpn_access", "title": "Доступ к VPN", "description": "Нужен доступ к VPN из дома.",
       "fields": {"urgency": "Высокая"}}


def _key() -> str:
    return str(uuid.uuid4())


async def test_ticket_types_schema(stand, tokens):
    body = await mcp_call("sd", "sd_list_ticket_types", {}, tokens["ivanov"])
    items = assert_list(body, {"type_id", "name", "fields"})
    assert len(items) == 5
    vpn = next(t for t in items if t["type_id"] == "vpn_access")
    assert vpn["name"] == "Доступ и VPN"
    for field in vpn["fields"]:
        assert {"name", "label", "required", "type"} <= set(field)
    urgency = next(f for f in vpn["fields"] if f["name"] == "urgency")
    assert urgency["required"] is True and urgency["options"]


async def test_create_ticket_schema_and_author_from_token(stand, tokens):
    body = await mcp_call("sd", "sd_create_ticket", VPN | {"idempotency_key": _key()}, tokens["ivanov"])
    assert "error" not in body, body
    assert {"number", "status", "url"} <= set(body)
    assert body["status"] == "new" and body["number"].startswith("SD-")
    assert body["url"].endswith(f"/ui/tickets/{body['number']}")

    resp = await stand.mock.get(f"/sd/tickets/{body['number']}", headers=bearer(tokens["ivanov"]))
    assert resp.status_code == 200
    assert resp.json()["author"]["name"].startswith("Иванов")


@pytest.mark.parametrize("case", ["required_field", "no_key", "short_key", "extra", "unknown_type",
                                  "employee"])
async def test_invalid_input(stand, tokens, case):
    args = {
        "required_field": VPN | {"fields": {}, "idempotency_key": _key()},
        "no_key": dict(VPN),
        "short_key": VPN | {"idempotency_key": "abc"},
        "extra": VPN | {"idempotency_key": _key(), "author": "petrova"},
        "unknown_type": VPN | {"type_id": "no_such_type", "idempotency_key": _key()},
        "employee": VPN | {"idempotency_key": _key(), "employee": "petrova"},
    }[case]
    body = await mcp_call("sd", "sd_create_ticket", args, tokens["ivanov"])
    assert error_code(body) == "invalid_input"
    resp = await stand.mock.get("/sd/tickets", params={"role": "author"}, headers=bearer(tokens["ivanov"]))
    assert all(t["title"] != VPN["title"] for t in resp.json()["items"])


async def test_rest_requires_idempotency_key(stand, tokens):
    resp = await stand.mock.post("/sd/tickets", json=VPN, headers=bearer(tokens["ivanov"]))
    assert_rest_error(resp, 400, "invalid_input")


async def test_no_token_and_expired_forbidden(stand, expired_token):
    for token in (None, expired_token):
        body = await mcp_call("sd", "sd_create_ticket", VPN | {"idempotency_key": _key()}, token)
        assert error_code(body) == "forbidden"
        assert error_code(await mcp_call("sd", "sd_list_ticket_types", {}, token)) == "forbidden"


async def test_same_key_gives_one_ticket(stand, tokens):
    key = _key()
    first = await mcp_call("sd", "sd_create_ticket", VPN | {"idempotency_key": key}, tokens["ivanov"])
    second = await mcp_call("sd", "sd_create_ticket", VPN | {"idempotency_key": key}, tokens["ivanov"])
    assert first["number"] == second["number"]

    headers = bearer(tokens["ivanov"]) | {"Idempotency-Key": key}
    resp = await stand.mock.post("/sd/tickets", json=VPN, headers=headers)
    assert resp.status_code == 200 and resp.json()["number"] == first["number"]

    new_key = bearer(tokens["ivanov"]) | {"Idempotency-Key": _key()}
    resp = await stand.mock.post("/sd/tickets", json=VPN, headers=new_key)
    assert resp.status_code == 201 and resp.json()["number"] != first["number"]

    listed = await stand.mock.get("/sd/tickets", params={"role": "author"}, headers=bearer(tokens["ivanov"]))
    assert sum(t["number"] == first["number"] for t in listed.json()["items"]) <= 1


async def test_foreign_key_is_not_reused(stand, tokens):
    """Чужой доступ: ключ чужого обращения не возвращает его Петровой."""
    key = _key()
    created = await mcp_call("sd", "sd_create_ticket", VPN | {"idempotency_key": key}, tokens["ivanov"])
    body = await mcp_call("sd", "sd_create_ticket", VPN | {"idempotency_key": key}, tokens["petrova"])
    assert error_code(body) == "forbidden"
    assert created["number"] not in str(body)
