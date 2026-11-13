"""T042: портал документации заглушки — только для токена role: indexer."""

from __future__ import annotations

from tests.conftest import bearer
from tests.contract.helpers import assert_rest_error


async def test_pages_list_for_indexer(stand, tokens):
    resp = await stand.mock.get("/confluence/pages", headers=bearer(tokens["indexer"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_more"] is False and len(body["items"]) >= 15
    for page in body["items"]:
        assert {"page_id", "version", "updated_at", "acl_updated_at"} <= set(page)
        assert isinstance(page["version"], int)


async def test_page_full_for_indexer(stand, tokens):
    resp = await stand.mock.get("/confluence/pages/mgmt-bonus", headers=bearer(tokens["indexer"]))
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert {"page_id", "title", "body", "allowed_groups", "version", "updated_at"} <= set(page)
    assert page["page_id"] == "mgmt-bonus"
    assert page["allowed_groups"] == ["managers"]
    assert page["body"].strip()


async def test_missing_page_not_found(stand, tokens):
    resp = await stand.mock.get("/confluence/pages/no-such-page", headers=bearer(tokens["indexer"]))
    assert_rest_error(resp, 404, "not_found")


async def test_employee_token_forbidden(stand, tokens):
    for path in ("/confluence/pages", "/confluence/pages/hr-vacation"):
        for who in ("ivanov", "kuznetsova"):
            resp = await stand.mock.get(path, headers=bearer(tokens[who]))
            assert_rest_error(resp, (401, 403), "forbidden")
        resp = await stand.mock.get(path, headers=bearer(tokens["recs-service"]))
        assert_rest_error(resp, (401, 403), "forbidden")


async def test_no_token_and_expired_forbidden(stand, expired_token):
    for path in ("/confluence/pages", "/confluence/pages/hr-vacation"):
        assert_rest_error(await stand.mock.get(path), (401, 403), "forbidden")
        resp = await stand.mock.get(path, headers=bearer(expired_token))
        assert_rest_error(resp, (401, 403), "forbidden")
