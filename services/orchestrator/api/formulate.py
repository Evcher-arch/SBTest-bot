"""POST /formulate: текст уведомления по готовым сигналам.

Модель только формулирует (принцип IV). Числа и названия в тексте модели сверяются с фактами;
при расхождении или сбое модели возвращается текст по шаблонам правил.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..clients import openai_client
from ..tracing import get_tracer

router = APIRouter()

INSTRUCTIONS = """\
Ты составляешь одно короткое уведомление в Telegram для сотрудника по списку сигналов.
Используй только факты из входных данных: не меняй и не добавляй числа, суммы, даты, номера \
и названия. Для каждого сигнала — одна строка с сутью и ссылкой url. Без приветствий и подписи. \
Пиши по-русски."""

NUMBER_RE = re.compile(r"\d[\d\s.,]*\d|\d")


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    object: str
    facts: dict[str, Any] = Field(default_factory=dict)
    template_text: str
    url: str | None = None


class FormulateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient_name: str
    signals: list[Signal] = Field(..., min_length=1, max_length=50)


def template_message(body: FormulateRequest) -> str:
    words = body.recipient_name.split()
    first = words[1] if len(words) > 1 else (words[0] if words else "")
    lines = [f"{first}, важное на сегодня:".lstrip(", ")]
    for s in body.signals:
        lines.append(f"• {s.template_text}" + (f"\n  {s.url}" if s.url else ""))
    return "\n".join(lines)


def _normalize(num: str) -> str:
    return re.sub(r"[\s.,]", "", num)


def facts_consistent(text: str, body: FormulateRequest) -> bool:
    """Каждое число в тексте модели должно встречаться в фактах, шаблонах или ссылках."""
    allowed = set()
    for s in body.signals:
        source = json.dumps(s.facts, ensure_ascii=False) + " " + s.template_text + " " + (s.url or "") + " " + s.object
        allowed.update(_normalize(n) for n in NUMBER_RE.findall(source))
        # суммы допускаются как с копейками, так и без
        allowed.update(_normalize(n).removesuffix("00") for n in NUMBER_RE.findall(source))
    for num in NUMBER_RE.findall(text):
        norm = _normalize(num)
        if norm and norm not in allowed and norm.removesuffix("00") not in allowed:
            return False
    for s in body.signals:
        if s.url and s.url not in text:
            return False
    return True


@router.post("/formulate")
async def formulate(body: FormulateRequest) -> dict:
    fallback = template_message(body)
    tracer = get_tracer()
    with tracer.trace("formulate", session_id="recommender", user_id=body.recipient_name,
                      input=body.model_dump()) as trace:
        try:
            payload = json.dumps(body.model_dump(), ensure_ascii=False)
            text = await openai_client.complete_text(instructions=INSTRUCTIONS, text=payload)
        except Exception as exc:  # noqa: BLE001
            trace.error(f"model failed: {type(exc).__name__}")
            return {"text": fallback, "source": "template"}
        if not text or not facts_consistent(text, body):
            trace.update(output={"text": fallback, "rejected": text}, level="WARNING",
                         status_message="facts mismatch")
            return {"text": fallback, "source": "template"}
        trace.update(output={"text": text})
        return {"text": text, "source": "model"}
