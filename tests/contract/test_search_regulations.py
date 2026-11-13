"""T043: search_regulations (POST /search сервиса поиска) — схема, ошибки, фильтр по группам."""

from __future__ import annotations

import pytest

from tests.conftest import bearer
from tests.contract.helpers import assert_rest_error

pytestmark = pytest.mark.integration

ITEM_KEYS = {"title", "section", "url", "updated_at", "text"}
MGMT_QUERY = "квартальная премия менеджера по продажам план продаж"


async def test_response_schema(full_stand, tokens):
    resp = await full_stand.search.post("/search", json={"query": "как оформить отпуск", "top_k": 3},
                                        headers=bearer(tokens["ivanov"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["has_more"], bool)
    assert 1 <= len(body["items"]) <= 3
    for item in body["items"]:
        assert ITEM_KEYS <= set(item)
        assert item["url"].startswith("http") and "/ui/pages/" in item["url"]
        assert item["updated_at"][:4].isdigit()
        assert item["text"].strip()


@pytest.mark.parametrize("payload", [
    {"query": "отпуск", "top_k": 0},
    {"query": "отпуск", "top_k": 9},
    {"query": "отпуск", "employee": "petrova"},
    {"top_k": 3},
])
async def test_invalid_input(full_stand, tokens, payload):
    resp = await full_stand.search.post("/search", json=payload, headers=bearer(tokens["ivanov"]))
    assert_rest_error(resp, 400, "invalid_input")


async def test_no_token_and_expired_forbidden(full_stand, expired_token):
    payload = {"query": "отпуск"}
    assert_rest_error(await full_stand.search.post("/search", json=payload), (401, 403), "forbidden")
    resp = await full_stand.search.post("/search", json=payload, headers=bearer(expired_token))
    assert_rest_error(resp, (401, 403), "forbidden")


async def test_foreign_access_mgmt_page(full_stand, tokens):
    """Чужой доступ: страница пространства MGMT не видна Иванову, видна Кузнецовой."""
    payload = {"query": MGMT_QUERY, "top_k": 8}
    ivanov = await full_stand.search.post("/search", json=payload, headers=bearer(tokens["ivanov"]))
    assert ivanov.status_code == 200, ivanov.text
    assert all("/ui/pages/mgmt-" not in i["url"] for i in ivanov.json()["items"])
    assert "премирован" not in ivanov.text.lower()

    boss = await full_stand.search.post("/search", json=payload, headers=bearer(tokens["kuznetsova"]))
    assert boss.status_code == 200, boss.text
    assert any(i["url"].endswith("/ui/pages/mgmt-bonus") for i in boss.json()["items"])
