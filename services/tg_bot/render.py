"""Оформление ответов: текст, источники с датой обновления, черновик обращения."""

from __future__ import annotations

from datetime import date

MAX_MESSAGE = 4000


def _ru_date(value: str) -> str:
    try:
        return date.fromisoformat(value[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return value


def format_sources(sources: list[dict]) -> str:
    if not sources:
        return ""
    lines = ["", "Источники:"]
    for s in sources:
        lines.append(f"• {s.get('title')} (обновлено {_ru_date(s.get('updated_at') or '')})\n  {s.get('url')}")
    return "\n".join(lines)


def format_answer(response: dict) -> str:
    return (response.get("text") or "").strip() + format_sources(response.get("sources") or [])


def format_draft(draft: dict) -> str:
    lines = [
        "📝 Черновик обращения в Service-Desk",
        f"Тип: {draft.get('type_name')}",
        f"Заголовок: {draft.get('title')}",
        f"Описание: {draft.get('description')}",
    ]
    for field in draft.get("fields") or []:
        lines.append(f"{field.get('label')}: {field.get('value')}")
    lines.append("")
    lines.append("Обращение будет создано только после нажатия «Создать».")
    return "\n".join(lines)


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts
