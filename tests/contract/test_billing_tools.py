"""T057: billing_list_invoices и billing_get_invoice — схемы, ошибки, чужой доступ."""

from __future__ import annotations

import pytest

from tests.contract.helpers import assert_list, error_code, mcp_call

LIST_KEYS = {"number", "date", "due_date", "amount", "status", "overdue_days", "url"}
INVOICE_KEYS = LIST_KEYS | {"payments"}
STATUSES = {"issued", "partially_paid", "paid", "overdue", "cancelled"}


async def test_list_invoices_schema(stand, tokens):
    body = await mcp_call("billing", "billing_list_invoices", {"client_id": "C001"}, tokens["ivanov"])
    items = assert_list(body, LIST_KEYS)
    numbers = {i["number"] for i in items}
    assert {"СЧ-2026-0142", "СЧ-2026-0143"} <= numbers
    for item in items:
        assert item["status"] in STATUSES
        assert isinstance(item["amount"], (int, float))
        assert isinstance(item["overdue_days"], int) and item["overdue_days"] >= 0
        assert "/ui/invoices/" in item["url"]
    overdue = next(i for i in items if i["number"] == "СЧ-2026-0142")
    assert overdue["status"] == "overdue" and overdue["overdue_days"] == 5


async def test_list_filter_by_status(stand, tokens):
    body = await mcp_call("billing", "billing_list_invoices", {"client_id": "C001", "status": "paid"},
                          tokens["ivanov"])
    items = assert_list(body, LIST_KEYS)
    assert items and all(i["status"] == "paid" for i in items)


async def test_get_invoice_with_payments(stand, tokens):
    body = await mcp_call("billing", "billing_get_invoice", {"number": "СЧ-2026-0131"}, tokens["ivanov"])
    assert "error" not in body, body
    assert INVOICE_KEYS <= set(body)
    assert body["status"] == "paid" and body["overdue_days"] == 0
    assert body["payments"] and {"date", "amount"} <= set(body["payments"][0])

    # частично оплаченный счёт с прошедшим сроком показывается как просроченный, оплаты видны
    body = await mcp_call("billing", "billing_get_invoice", {"number": "СЧ-2026-0137"}, tokens["ivanov"])
    assert body["status"] == "overdue" and body["overdue_days"] == 15
    assert sum(p["amount"] for p in body["payments"]) == 100000


@pytest.mark.parametrize("name,args", [
    ("billing_list_invoices", {"client_id": "C001", "status": "late"}),
    ("billing_list_invoices", {}),
    ("billing_list_invoices", {"client_id": "C001", "employee": "petrova"}),
    ("billing_list_invoices", {"client_id": "C001", "period": {"from": "вчера"}}),
    ("billing_get_invoice", {}),
    ("billing_get_invoice", {"number": "СЧ-2026-0142", "extra": 1}),
])
async def test_invalid_input(stand, tokens, name, args):
    assert error_code(await mcp_call("billing", name, args, tokens["ivanov"])) == "invalid_input"


async def test_unknown_number_not_found(stand, tokens):
    body = await mcp_call("billing", "billing_get_invoice", {"number": "СЧ-2026-9999"}, tokens["ivanov"])
    assert error_code(body) == "not_found"


async def test_no_token_and_expired_forbidden(stand, expired_token):
    for token in (None, expired_token):
        body = await mcp_call("billing", "billing_get_invoice", {"number": "СЧ-2026-0142"}, token)
        assert error_code(body) == "forbidden"
        body = await mcp_call("billing", "billing_list_invoices", {"client_id": "C001"}, token)
        assert error_code(body) == "forbidden"


async def test_foreign_invoice(stand, tokens):
    """Чужой доступ: счёт клиента Петровой — forbidden для Иванова, данные для Кузнецовой."""
    body = await mcp_call("billing", "billing_get_invoice", {"number": "СЧ-2026-0202"}, tokens["ivanov"])
    assert error_code(body) == "forbidden"
    body = await mcp_call("billing", "billing_list_invoices", {"client_id": "C006"}, tokens["ivanov"])
    assert error_code(body) == "forbidden"

    body = await mcp_call("billing", "billing_get_invoice", {"number": "СЧ-2026-0202"},
                          tokens["kuznetsova"])
    assert "error" not in body and body["number"] == "СЧ-2026-0202"
    body = await mcp_call("billing", "billing_list_invoices", {"client_id": "C006"}, tokens["kuznetsova"])
    assert assert_list(body, LIST_KEYS)
