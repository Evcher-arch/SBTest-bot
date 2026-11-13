"""Выпуск токена сотрудника на каждое сообщение и нажатие кнопки.

Токен кладётся в Redis token:{session_id} на 10 минут; оркестратору передаётся только
идентификатор сессии. Токен не логируется.
"""

from __future__ import annotations

from common import directory
from common.jwt import MAX_TTL_S, issue_token
from common.redis_client import get_redis


async def issue_for_session(employee_id: str, session_id: str) -> None:
    emp = directory.get_employee(employee_id)
    if emp is None:
        raise ValueError(f"unknown demo employee {employee_id}")
    token = issue_token(
        emp.id, role="employee", name=emp.name, groups=emp.groups,
        subordinates=directory.subordinates_of(emp.id), ttl_s=MAX_TTL_S,
    )
    await get_redis().set(f"token:{session_id}", token, ex=MAX_TTL_S)
