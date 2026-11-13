"""Справочник демо-сотрудников (замена корпоративного каталога).

Используется только ботом (выпуск токена) и сервисом рекомендаций (выбор получателя).
Решений о доступе по справочнику не принимается.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from .settings import get_settings


@dataclass(frozen=True)
class Employee:
    id: str
    name: str
    position: str
    groups: list[str] = field(default_factory=list)
    manager_id: str | None = None


@lru_cache(maxsize=1)
def _load() -> dict[str, Employee]:
    path = get_settings().seed_path / "employees.yaml"
    with path.open(encoding="utf-8") as fh:
        rows = yaml.safe_load(fh) or []
    return {
        r["id"]: Employee(
            id=r["id"],
            name=r["name"],
            position=r.get("position", ""),
            groups=list(r.get("groups") or []),
            manager_id=r.get("manager_id"),
        )
        for r in rows
    }


def all_employees() -> list[Employee]:
    return list(_load().values())


def get_employee(employee_id: str) -> Employee | None:
    return _load().get(employee_id)


def subordinates_of(employee_id: str) -> list[str]:
    return [e.id for e in _load().values() if e.manager_id == employee_id]


def manager_of(employee_id: str) -> str | None:
    emp = _load().get(employee_id)
    return emp.manager_id if emp else None


def reload() -> None:
    _load.cache_clear()
