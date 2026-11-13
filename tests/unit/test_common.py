"""Общий пакет: токены, формат ошибок, маскирование токена в логах."""

from __future__ import annotations

import logging

import pytest
from common.errors import ApiError, error_body
from common.jwt import MAX_TTL_S, issue_service_token, issue_token, verify_token
from common.logging import SecretFilter, mask_secrets


def test_issue_and_verify_employee_token():
    token = issue_token("ivanov", name="Иванов", groups=["all", "sales"], subordinates=[])
    claims = verify_token(token)
    assert claims.sub == "ivanov"
    assert claims.role == "employee"
    assert claims.groups == ["all", "sales"]


def test_token_ttl_is_capped_at_ten_minutes():
    import time

    token = issue_token("ivanov", ttl_s=3600)
    claims = verify_token(token)
    assert claims.exp - int(time.time()) <= MAX_TTL_S


def test_service_token_roles():
    assert verify_token(issue_service_token("recs-service")).role == "recs-service"
    assert verify_token(issue_service_token("indexer")).role == "indexer"
    with pytest.raises(ValueError):
        issue_service_token("employee")


def test_expired_token_is_forbidden(expired_token):
    with pytest.raises(ApiError) as err:
        verify_token(expired_token)
    assert err.value.code == "forbidden"


@pytest.mark.parametrize("token", [None, "", "garbage", "a.b.c"])
def test_missing_or_invalid_token_is_forbidden(token):
    with pytest.raises(ApiError) as err:
        verify_token(token)
    assert err.value.code == "forbidden"


def test_token_signed_with_other_secret_is_forbidden():
    token = issue_token("ivanov", secret="another-secret-0123456789abcdef0123456789")
    with pytest.raises(ApiError):
        verify_token(token)


def test_error_body_format():
    body = error_body("not_found", "Счёт не найден.")
    assert body == {"error": {"code": "not_found", "message": "Счёт не найден."}}
    assert ApiError("unavailable").status == 503
    assert ApiError("forbidden").status == 403
    assert ApiError("invalid_input").status == 400
    with pytest.raises(ValueError):
        ApiError("teapot")


def test_token_is_masked_in_logs(caplog):
    token = issue_token("ivanov")
    logger = logging.getLogger("test.mask")
    caplog.handler.addFilter(SecretFilter())
    with caplog.at_level(logging.INFO, logger="test.mask"):
        logger.info("call with Authorization: Bearer %s", token)
        logger.info("raw token %s in text", token)
    text = caplog.text
    assert token not in text
    assert "***" in text


def test_mask_secrets_header_forms():
    token = issue_token("ivanov")
    assert token not in mask_secrets(f"{{'authorization': 'Bearer {token}'}}")
    assert token not in mask_secrets(f"Authorization={token}")
