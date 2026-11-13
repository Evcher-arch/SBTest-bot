"""История 1 — вопрос о процессе: приёмочные сценарии 1–5 (T044).

Поиск идёт по настоящему индексу в PostgreSQL (pgvector), эмбеддинги — детерминированная
подмена. Модель подменена: она цитирует фрагменты по номеру [n], а ссылку и дату
источника формирует код.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta

import pytest
from common.settings import get_settings, reset_settings

from services.orchestrator.agent import prompts
from tests.support import fn_call, response, text_msg

pytestmark = pytest.mark.integration


def fragments(body: dict) -> list[dict]:
    outputs = [json.loads(i["output"]) for i in body["input"]
               if isinstance(i, dict) and i.get("type") == "function_call_output"]
    return outputs[-1].get("items", []) if outputs else []


def cite(title_part: str, answer: str = "Шаги: подайте заявление") -> Callable[[dict], dict]:
    """Модель отвечает, ссылаясь на фрагмент, в заголовке которого есть title_part."""

    def handler(body: dict) -> dict:
        items = fragments(body)
        refs = [f["ref"] for f in items if title_part.lower() in (f.get("title") or "").lower()]
        if not refs:
            return response(text_msg("Регламент по этому вопросу не найден."))
        return response(text_msg(f"{answer} [{refs[0]}]. Подробнее — по ссылке http://evil.example/fake [99]."))

    return handler


def days_ago(n: int) -> str:
    return (get_settings().now().date() - timedelta(days=n)).isoformat()


async def test_s1_answer_with_source_link_and_date(full_stand):
    stand = full_stand
    stand.openai.push(response(fn_call("search_regulations", {"query": "как оформить отпуск ежегодный"})),
                      cite("отпуск"))
    result = await stand.chat("ivanov", "Как оформить отпуск?")

    assert result["sources"], result
    [source] = result["sources"]
    assert source["url"].endswith("/ui/pages/hr-vacation")
    assert source["updated_at"] == days_ago(9)
    assert "отпуск" in source["title"].lower()
    # номера фрагментов убраны из текста, выдуманная моделью ссылка в sources не попала
    assert "[1]" not in result["text"] and "[99]" not in result["text"]
    assert all("evil.example" not in s["url"] for s in result["sources"])
    # модель получила фрагменты без url: ссылки формирует только код
    for frag in fragments(stand.openai.requests[-1]):
        assert "url" not in frag
        assert {"ref", "title", "section", "updated_at", "text"} <= set(frag)


async def test_s2_closed_page_not_disclosed_to_employee(full_stand):
    stand = full_stand
    stand.openai.push(
        response(fn_call("search_regulations", {"query": "порядок премирования квартальная премия"})),
        cite("премирован"),
    )
    result = await stand.chat("ivanov", "Как начисляется квартальная премия?")
    items = fragments(stand.openai.requests[-1])
    assert all("премирован" not in (f.get("title") or "").lower() for f in items)
    assert "Квартальная премия менеджера" not in stand.openai.all_request_text()
    assert all("mgmt-" not in s["url"] for s in result["sources"])
    if not items:
        assert result["text"] == prompts.REGULATION_NOT_FOUND


async def test_s3_manager_gets_closed_page(full_stand):
    stand = full_stand
    stand.openai.push(
        response(fn_call("search_regulations", {"query": "порядок премирования квартальная премия"})),
        cite("премирован", "Премия до 30 % оклада"),
    )
    result = await stand.chat("kuznetsova", "Как начисляется квартальная премия?")
    urls = [s["url"] for s in result["sources"]]
    assert any(u.endswith("/ui/pages/mgmt-bonus") for u in urls), result
    bonus = next(s for s in result["sources"] if s["url"].endswith("mgmt-bonus"))
    assert bonus["updated_at"] == days_ago(16)


async def test_s4_no_regulation_offers_ticket(full_stand, monkeypatch):
    # порог близости как в рабочей конфигурации (.env.example): в тестах он понижен,
    # и случайные совпадения хэшей подменных эмбеддингов давали бы «находки»
    monkeypatch.setenv("SEARCH_MIN_SIMILARITY", "0.3")
    reset_settings()
    stand = full_stand
    stand.openai.push(
        response(fn_call("search_regulations", {"query": "щщщщ ъъъъ жжжж"})),
        response(text_msg("Можно сделать так-то [1].")),
    )
    result = await stand.chat("ivanov", "Как вызвать единорога в переговорную?")
    assert fragments(stand.openai.requests[-1]) == []
    # ответ по пустому поиску заменяется кодом, даже если модель что-то сочинила
    assert result["text"] == prompts.REGULATION_NOT_FOUND
    assert "не найден" in result["text"]
    assert "обращение" in result["text"]
    assert result["sources"] == []
    monkeypatch.delenv("SEARCH_MIN_SIMILARITY")
    reset_settings()


async def test_s5_page_edit_reaches_answer_after_reindex(full_stand):
    stand = full_stand
    new_body = (
        "# Порядок оформления ежегодного отпуска\n\n"
        "## Новая редакция\n\n"
        "С этого месяца заявление на отпуск подаётся через портал самообслуживания "
        "не позднее чем за 21 календарный день до начала отпуска."
    )
    resp = await stand.mock.patch("/admin/pages/hr-vacation", json={"body": new_body})
    assert resp.status_code == 200, resp.text
    resp = await stand.search.post("/admin/reindex")
    assert resp.status_code == 200
    assert resp.json()["pages_updated"] >= 1

    stand.openai.push(
        response(fn_call("search_regulations", {"query": "заявление на отпуск портал самообслуживания"})),
        cite("отпуск"),
    )
    result = await stand.chat("ivanov", "Как теперь оформить отпуск?")
    items = fragments(stand.openai.requests[-1])
    vacation = [f for f in items if "отпуск" in (f.get("title") or "").lower()]
    assert vacation
    assert any("портал самообслуживания" in f["text"] for f in vacation)
    assert not any("28 календарных дней" in f["text"] for f in vacation)  # старая редакция удалена
    [source] = [s for s in result["sources"] if s["url"].endswith("hr-vacation")]
    assert source["updated_at"] == get_settings().now().date().isoformat()
