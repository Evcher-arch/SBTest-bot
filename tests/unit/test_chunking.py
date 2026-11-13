"""Нарезка регламентов по заголовкам Markdown."""

from __future__ import annotations

from services.search.chunking import chunk_markdown

BODY = """# Регламент отпуска

## Общие положения

Отпуск — 28 дней.

## Порядок оформления

1. Согласуйте даты.
2. Оформите заявление.

### Сроки

За 14 дней.
"""


def test_chunks_follow_headings():
    chunks = chunk_markdown("Регламент отпуска", BODY)
    paths = [c.heading_path for c in chunks]
    assert paths == [
        "Регламент отпуска / Общие положения",
        "Регламент отпуска / Порядок оформления",
        "Регламент отпуска / Порядок оформления / Сроки",
    ]
    assert "28 дней" in chunks[0].text
    assert "Оформите заявление" in chunks[1].text


def test_heading_path_uses_separator_and_resets_on_same_level():
    body = "# A\n\n## B\n\nтекст b\n\n### C\n\nтекст c\n\n## D\n\nтекст d\n"
    paths = [c.heading_path for c in chunk_markdown("A", body)]
    assert paths == ["A / B", "A / B / C", "A / D"]


def test_text_before_first_heading_uses_title():
    chunks = chunk_markdown("Страница", "Вступление без заголовка.\n\n## Раздел\n\nТекст.")
    assert chunks[0].heading_path == "Страница"
    assert chunks[0].text == "Вступление без заголовка."


def test_long_section_split_with_overlap():
    sentences = " ".join(f"Предложение номер {i} о порядке работы." for i in range(200))
    chunks = chunk_markdown("T", f"## Длинный раздел\n\n{sentences}", max_chars=500, overlap=100)
    assert len(chunks) > 3
    assert all(len(c.text) <= 500 for c in chunks)
    assert all(c.heading_path == "Длинный раздел" for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        tail_words = prev.text.split()[-3:]
        assert any(w in nxt.text for w in tail_words), "соседние фрагменты должны перекрываться"


def test_empty_sections_are_skipped():
    chunks = chunk_markdown("T", "# T\n\n## Пустой\n\n## Непустой\n\nтекст")
    assert [c.heading_path for c in chunks] == ["T / Непустой"]
