"""Выпуск и проверка JWT HS256 сотрудника и сервисных учётных записей."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import jwt as pyjwt

from .errors import ApiError
from .settings import get_settings

ALGORITHM = "HS256"
MAX_TTL_S = 600  # «не более 10 минут от выпуска»
ROLES = ("employee", "recs-service", "indexer")


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    role: str
    name: str = ""
    groups: list[str] = field(default_factory=list)
    subordinates: list[str] = field(default_factory=list)
    exp: int = 0


def issue_token(
    sub: str,
    *,
    role: str = "employee",
    name: str = "",
    groups: list[str] | None = None,
    subordinates: list[str] | None = None,
    ttl_s: int = MAX_TTL_S,
    secret: str | None = None,
) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    ttl_s = min(ttl_s, MAX_TTL_S)
    now = int(time.time())
    payload = {
        "sub": sub,
        "name": name,
        "groups": list(groups or []),
        "subordinates": list(subordinates or []),
        "role": role,
        "iat": now,
        "exp": now + ttl_s,
    }
    return pyjwt.encode(payload, secret or get_settings().jwt_secret, algorithm=ALGORITHM)


def issue_service_token(role: str) -> str:
    """Сервисный токен: recs-service (выборки) или indexer (чтение страниц)."""
    if role not in ("recs-service", "indexer"):
        raise ValueError(f"not a service role: {role}")
    return issue_token(role, role=role, name=role)


def verify_token(token: str | None, *, secret: str | None = None) -> TokenClaims:
    if not token:
        raise ApiError("forbidden", "Требуется токен доступа.", status=401)
    try:
        data = pyjwt.decode(
            token,
            secret or get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "role"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise ApiError("forbidden", "Срок действия токена истёк.", status=401) from exc
    except pyjwt.PyJWTError as exc:
        raise ApiError("forbidden", "Токен доступа недействителен.", status=401) from exc
    if data.get("role") not in ROLES:
        raise ApiError("forbidden", "Недопустимая роль в токене.", status=401)
    return TokenClaims(
        sub=str(data["sub"]),
        role=str(data["role"]),
        name=str(data.get("name", "")),
        groups=list(data.get("groups") or []),
        subordinates=list(data.get("subordinates") or []),
        exp=int(data["exp"]),
    )


def bearer_from_header(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
