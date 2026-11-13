"""US5: наблюдаемость. Трасса на сообщение, шаги модели и инструментов, сессия, оценки,
отказоустойчивость к Langfuse и отсутствие токена в трассах, запросах к модели и истории."""

from __future__ import annotations

import os

import pytest
from common.redis_client import get_redis

from services.orchestrator.tracing import Tracer, set_tracer
from tests.conftest import FakeLangfuse
from tests.support import fn_call, response, text_msg

pytestmark = pytest.mark.integration


def _names(lf: FakeLangfuse, trace_id: str) -> list[str]:
    return [o.name for o in lf.children(trace_id)]


async def test_one_trace_per_message_with_model_and_tool_steps(stand):
    stand.openai.push(
        response(fn_call("crm_find_client", {"name": "Ромашка"}),
                 fn_call("billing_get_invoice", {"number": "СЧ-0000-0000"})),
        response(text_msg("Клиент найден, счёта с таким номером нет.")),
    )
    result = await stand.chat("ivanov", "Найди Ромашку и счёт СЧ-0000-0000")
    lf = stand.langfuse
    traces = lf.traces()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.name == "chat" and trace.trace_id == result["trace_id"]
    assert trace.input == "Найди Ромашку и счёт СЧ-0000-0000"
    assert trace.output["text"] == result["text"]
    assert trace.output["tools"] == ["crm_find_client", "billing_get_invoice"]

    names = _names(lf, trace.trace_id)
    assert names.count("model:agent-step-1") == 1 and names.count("model:agent-step-2") == 1
    assert "tool:crm_find_client" in names and "tool:billing_get_invoice" in names

    model_step = next(o for o in lf.children(trace.trace_id) if o.name == "model:agent-step-1")
    assert model_step.output == {"tool_calls": ["crm_find_client", "billing_get_invoice"]}
    assert model_step.metadata["usage"]["total_tokens"] == 120
    assert "duration_ms" in model_step.metadata

    ok = next(o for o in lf.children(trace.trace_id) if o.name == "tool:crm_find_client")
    assert ok.input["arguments"] and "Ромашка" in ok.input["arguments"]
    assert ok.output["items"] and ok.output["items"][0]["client_id"] == "C001"
    assert ok.metadata["error_code"] is None and "duration_ms" in ok.metadata

    failed = next(o for o in lf.children(trace.trace_id) if o.name == "tool:billing_get_invoice")
    assert failed.metadata["error_code"] == "not_found"
    assert failed.level == "ERROR" and failed.status_message == "not_found"
    assert all(o.ended for o in lf.observations)


async def test_search_step_is_traced(stand):
    stand.openai.push(response(fn_call("search_regulations", {"query": "отпуск"})),
                      response(text_msg("Не удалось найти.")))
    result = await stand.chat("ivanov", "Как оформить отпуск?")
    step = next(o for o in stand.langfuse.children(result["trace_id"])
                if o.name == "tool:search_regulations")
    # Сервис поиска в этом стенде не поднят — ошибка шага видна в трассе
    assert step.metadata["error_code"] == "unavailable" and step.level == "ERROR"


async def test_dialog_traces_share_session_id(stand):
    stand.openai.push(response(text_msg("Здравствуйте!")), response(text_msg("Чем помочь?")))
    first = await stand.chat("ivanov", "Привет", session_id="dialog-1")
    second = await stand.chat("ivanov", "Ещё вопрос", session_id="dialog-1")
    assert first["trace_id"] != second["trace_id"]
    traces = {t.trace_id: t for t in stand.langfuse.traces()}
    assert traces[first["trace_id"]].metadata["session_id"] == "dialog-1"
    assert traces[second["trace_id"]].metadata["session_id"] == "dialog-1"


async def test_feedback_attaches_score_to_trace(stand):
    stand.openai.push(response(text_msg("Ответ.")))
    result = await stand.chat("ivanov", "Вопрос")
    resp = await stand.orchestrator.post("/feedback", json={"trace_id": result["trace_id"], "value": -1})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    [score] = stand.langfuse.scores
    assert score["trace_id"] == result["trace_id"] and score["value"] == -1.0
    assert score["name"] == "user_feedback"

    bad = await stand.orchestrator.post("/feedback", json={"trace_id": result["trace_id"], "value": 5})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_input"


async def test_langfuse_down_does_not_affect_chat_and_feedback(stand):
    set_tracer(Tracer(client=FakeLangfuse(fail=True)))
    stand.openai.push(response(fn_call("crm_find_client", {"name": "Ромашка"})),
                      response(text_msg("Нашёл ООО «Ромашка».")))
    result = await stand.chat("ivanov", "Найди Ромашку")
    assert result["text"] == "Нашёл ООО «Ромашка»." and result["trace_id"]
    resp = await stand.orchestrator.post("/feedback", json={"trace_id": result["trace_id"], "value": 1})
    assert resp.status_code == 200 and resp.json() == {"ok": True}


async def test_token_never_reaches_trace_model_or_history(full_stand):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    stand = full_stand
    stand.openai.push(
        response(fn_call("crm_find_client", {"name": "Ромашка"}),
                 fn_call("search_regulations", {"query": "отпуск"})),
        response(text_msg("Готово.")),
    )
    await stand.chat("ivanov", "Найди Ромашку и регламент отпуска", session_id="tok-check")
    token = await get_redis().get("token:tok-check")
    assert token and token.count(".") == 2

    assert token not in stand.langfuse.dump()
    assert "Bearer" not in stand.langfuse.dump()
    assert token not in stand.openai.all_request_text()

    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT role, content::text AS c FROM message WHERE session_id = 'tok-check'"))).all()
    finally:
        await engine.dispose()
    assert {r.role for r in rows} >= {"user", "tool", "assistant"}
    for row in rows:
        assert token not in row.c and "Bearer" not in row.c
