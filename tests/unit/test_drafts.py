"""Переходы состояний черновика обращения (Redis подменён fakeredis)."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest
from common import redis_client

from services.orchestrator import drafts


@pytest.fixture(autouse=True)
def fake_redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_client.set_redis(client)
    yield client
    redis_client.set_redis(None)


async def _draft(session_id: str = "s1", employee_id: str = "ivanov") -> dict:
    return await drafts.create(
        session_id=session_id, employee_id=employee_id, type_id="vpn_access", type_name="Доступ и VPN",
        title="Не работает VPN", description="Не подключается", fields={"urgency": "Высокая"},
        field_labels={"urgency": "Срочность"}, trace_id="t1",
    )


async def test_create_pending_with_ttl(fake_redis):
    draft = await _draft()
    assert draft["status"] == "pending"
    ttl = await fake_redis.ttl(f"draft:{draft['action_id']}")
    assert 0 < ttl <= 600
    view = drafts.public_view(draft)
    assert view["fields"] == [{"label": "Срочность", "value": "Высокая"}]
    assert "session_id" not in view and "employee_id" not in view


async def test_pending_confirming_confirmed():
    draft = await _draft()
    aid = draft["action_id"]
    assert await drafts.try_begin_confirm(aid) == "ok"
    assert (await drafts.get(aid))["status"] == "confirming"
    await drafts.finish_confirm(aid, {"number": "SD-2000", "url": "u", "employee_id": "ivanov"})
    assert (await drafts.get(aid))["status"] == "confirmed"
    assert (await drafts.get_result(aid))["number"] == "SD-2000"
    # повтор для confirmed не начинает новое создание
    assert await drafts.try_begin_confirm(aid) == "confirmed"


async def test_error_returns_to_pending():
    aid = (await _draft())["action_id"]
    assert await drafts.try_begin_confirm(aid) == "ok"
    await drafts.fail_confirm(aid)
    assert (await drafts.get(aid))["status"] == "pending"
    assert await drafts.try_begin_confirm(aid) == "ok"


async def test_parallel_confirm_only_one_wins():
    aid = (await _draft())["action_id"]
    results = await asyncio.gather(*(drafts.try_begin_confirm(aid) for _ in range(5)))
    assert results.count("ok") == 1
    assert set(results) <= {"ok", "busy", "confirming"}


async def test_cancel_and_confirm_not_possible():
    aid = (await _draft())["action_id"]
    await drafts.cancel(aid)
    assert (await drafts.get(aid))["status"] == "cancelled"
    assert await drafts.try_begin_confirm(aid) == "cancelled"


async def test_expired_draft_is_missing(fake_redis):
    aid = (await _draft())["action_id"]
    await fake_redis.delete(f"draft:{aid}")  # истечение TTL
    assert await drafts.get(aid) is None
    assert await drafts.try_begin_confirm(aid) == "missing"


async def test_cancel_session_cancels_only_pending():
    d1 = await _draft("s1")
    d2 = await _draft("s1")
    other = await _draft("s2")
    await drafts.try_begin_confirm(d2["action_id"])
    await drafts.finish_confirm(d2["action_id"], {"number": "SD-1", "url": "u", "employee_id": "ivanov"})
    assert await drafts.cancel_session("s1") == 1
    assert (await drafts.get(d1["action_id"]))["status"] == "cancelled"
    assert (await drafts.get(d2["action_id"]))["status"] == "confirmed"
    assert (await drafts.get(other["action_id"]))["status"] == "pending"


async def test_clear_all_removes_drafts_and_results():
    d = await _draft()
    await drafts.try_begin_confirm(d["action_id"])
    await drafts.finish_confirm(d["action_id"], {"number": "SD-1", "url": "u", "employee_id": "ivanov"})
    await _draft()
    assert await drafts.clear_all() == 3
    assert await drafts.get(d["action_id"]) is None
    assert await drafts.get_result(d["action_id"]) is None
