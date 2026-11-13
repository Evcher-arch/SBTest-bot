"""Нарезка Markdown по заголовкам; длинные разделы делятся с перекрытием."""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SEPARATOR = " / "


@dataclass(frozen=True)
class Chunk:
    heading_path: str
    text: str


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", start, end), text.rfind(". ", start, end), text.rfind(" ", start, end))
            if cut > start + max_chars // 2:
                end = cut + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        next_start = end - overlap
        space = text.find(" ", next_start, end)
        start = space + 1 if space != -1 else next_start
    return [p for p in parts if p]


def chunk_markdown(title: str, body: str, *, max_chars: int = 1200, overlap: int = 200) -> list[Chunk]:
    """Разделы Markdown → фрагменты с путём заголовков через « / »."""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] = []
    current_path = title

    def flush() -> None:
        content = "\n".join(current).strip()
        if content:
            sections.append((current_path, [content]))

    for line in body.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            current = []
            level, heading = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            current_path = SEPARATOR.join(h for _, h in stack)
        else:
            current.append(line)
    flush()

    chunks: list[Chunk] = []
    for path, (content,) in sections:
        for piece in _split_long(content, max_chars, overlap):
            chunks.append(Chunk(heading_path=path, text=piece))
    return chunks
