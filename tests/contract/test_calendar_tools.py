"""T059: calendar_list_my_events — схема, интервал, только встречи владельца токена."""

from __future__ import annotations

from datetime import timedelta

import pytest
from common.settings import get_settings

from tests.contract.helpers import assert_list, error_code, mcp_call

ITEM_KEYS = {"subject", "start", "end", "organizer", "attendees"}


def _range(days_from: int, days_to: int) -> dict:
    now = get_settings().now().replace(microsecond=0)
    start = (now + timedelta(days=days_from)).replace(hour=0, minute=0, second=0)
    return {"from": start.isoformat(), "to": (start + timedelta(days=days_to - days_from)).isoformat()}


async def _subjects(token: str, days_from: int = -1, days_to: int = 7) -> set[str]:
    body = await mcp_call("calendar", "calendar_list_my_events", _range(days_from, days_to), token)
    return {i["subject"] for i in assert_list(body, ITEM_KEYS)}


async def test_schema(stand, tokens):
    body = await mcp_call("calendar", "calendar_list_my_events", _range(0, 3), tokens["ivanov"])
    items = assert_list(body, ITEM_KEYS)
    assert items
    for item in items:
        assert isinstance(item["attendees"], list)
        assert item["organizer"]["name"]
        assert item["start"] < item["end"]


@pytest.mark.parametrize("case", ["too_long", "not_dates", "missing_to", "extra"])
async def test_invalid_input(stand, tokens, case):
    args = {
        "too_long": _range(0, 32),
        "not_dates": {"from": "завтра", "to": "послезавтра"},
        "missing_to": {"from": _range(0, 1)["from"]},
        "extra": _range(0, 1) | {"employee": "petrova"},
    }[case]
    body = await mcp_call("calendar", "calendar_list_my_events", args, tokens["ivanov"])
    assert error_code(body) == "invalid_input"


async def test_no_token_and_expired_forbidden(stand, expired_token):
    for token in (None, expired_token):
        body = await mcp_call("calendar", "calendar_list_my_events", _range(0, 1), token)
        assert error_code(body) == "forbidden"


async def test_only_own_events(stand, tokens):
    """Только встречи владельца токена; руководитель не видит календарь подчинённого."""
    petrova = await _subjects(tokens["petrova"])
    boss = await _subjects(tokens["kuznetsova"])
    ivanov = await _subjects(tokens["ivanov"])
    negotiation = "Переговоры по новому договору поставки"  # встреча Петровой с клиентом
    assert negotiation in petrova
    assert negotiation not in boss
    assert negotiation not in ivanov
    assert "Разбор воронки продаж за месяц" in ivanov
    assert "Разбор воронки продаж за месяц" in boss
    assert "Планёрка Service-Desk" not in ivanov | boss | petrova
