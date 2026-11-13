"""Цикл агента: модель предлагает, код исполняет (принцип II).

Запрос к модели → проверка имени по белому списку и параметров по схеме → исполнение →
результат модели. Лимиты: AGENT_MAX_STEPS шагов, TOOL_TIMEOUT_S на вызов, ANSWER_TIMEOUT_S
на весь ответ. sd_create_ticket не исполняется: создаётся черновик, цикл завершается.
Ссылки и даты источников формирует код по номерам фрагментов, которые указала модель.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from common.errors import ApiError, error_body
from common.settings import get_settings

from .. import drafts
from ..clients import openai_client
from ..db import get_history
from ..tracing import Observation, get_tracer
from . import prompts, registry
from .registry import ToolContext

log = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\s*\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass
class AgentResult:
    trace_id: str
    text: str
    sources: list[dict] = field(default_factory=list)
    draft: dict | None = None
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class _State:
    search_calls: int = 0
    search_hits: int = 0
    other_calls: int = 0
    fragments: dict[int, dict] = field(default_factory=dict)
    draft: dict | None = None
    calls: list[dict] = field(default_factory=list)


class ModelUnavailable(Exception):
    pass


def _error_code(result: Any) -> str | None:
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        return result["error"].get("code")
    return None


def _as_input_item(item: Any) -> dict:
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return dict(item)


class Agent:
    def __init__(self, session_id: str, employee_id: str) -> None:
        self.ctx = ToolContext(session_id=session_id, employee_id=employee_id)
        self.state = _State()
        self.settings = get_settings()
        self.tracer = get_tracer()
        self.history = get_history()
        self.trace_id: str | None = None

    # --- вызов инструмента -------------------------------------------------
    async def _handle_call(self, call: Any) -> dict:
        name = getattr(call, "name", "")
        raw_args = getattr(call, "arguments", "") or "{}"
        started = time.monotonic()
        with self.tracer.step(f"tool:{name}", as_type="tool", input={"name": name, "arguments": raw_args}) as obs:
            result = await self._run_tool(name, raw_args, obs)
            code = _error_code(result)
            obs.update(
                output=result,
                metadata={"duration_ms": int((time.monotonic() - started) * 1000), "error_code": code},
                **({"level": "ERROR", "status_message": code} if code else {}),
            )
        self.state.calls.append({"name": name, "arguments": raw_args, "error": code})
        await self._remember("tool", {"name": name, "arguments": raw_args, "error": code,
                                      "result": _shorten(result)})
        return result

    async def _run_tool(self, name: str, raw_args: str, obs: Observation) -> dict:
        if not registry.is_allowed(name):
            return error_body("invalid_input", f"Инструмент {name} не разрешён.")
        try:
            args = json.loads(raw_args)
        except ValueError:
            return error_body("invalid_input", "Параметры инструмента — не JSON.")
        try:
            registry.validate(name, args)
        except ApiError as exc:
            return exc.body()
        spec = registry.REGISTRY[name]
        if spec.intercept:
            try:
                return await self._prepare_draft(args, obs)
            except TimeoutError:
                return error_body("unavailable", "Service-Desk не ответил вовремя.")
        try:
            result = await asyncio.wait_for(registry.execute(name, self.ctx, args),
                                            timeout=self.settings.tool_timeout_s)
        except TimeoutError:
            result = error_body("unavailable", "Система не ответила вовремя.")
        except Exception:  # noqa: BLE001
            log.exception("tool %s failed", name)
            result = error_body("unavailable", "Ошибка при обращении к системе.")
        if name == "search_regulations":
            return self._register_fragments(result)
        self.state.other_calls += 1
        return result

    def _register_fragments(self, result: dict) -> dict:
        self.state.search_calls += 1
        if _error_code(result):
            self.state.other_calls += 1  # ошибка поиска — не «регламент не найден»
            return result
        items = []
        for item in result.get("items", []):
            ref = len(self.state.fragments) + 1
            self.state.fragments[ref] = item
            items.append({"ref": ref, "title": item.get("title"), "section": item.get("section"),
                          "updated_at": item.get("updated_at"), "text": item.get("text")})
        self.state.search_hits += len(items)
        return {"items": items, "note": "Указывай номера ref использованных фрагментов в квадратных скобках."}

    async def _prepare_draft(self, args: dict, obs: Observation) -> dict:
        types = await asyncio.wait_for(
            registry.execute("sd_list_ticket_types", self.ctx, {}), timeout=self.settings.tool_timeout_s
        )
        if _error_code(types):
            return types
        ttype = next((t for t in types.get("items", []) if t["type_id"] == args["type_id"]), None)
        if ttype is None:
            return error_body("invalid_input", f"Неизвестный тип обращения {args['type_id']}. "
                                               "Выберите тип из sd_list_ticket_types.")
        values = dict(args.get("fields") or {})
        labels = {f["name"]: f["label"] for f in ttype["fields"]}
        unknown = set(values) - set(labels)
        if unknown:
            return error_body("invalid_input", f"У типа «{ttype['name']}» нет полей: {', '.join(sorted(unknown))}.")
        for f in ttype["fields"]:
            value = values.get(f["name"])
            if f.get("required") and value in (None, ""):
                return error_body("invalid_input", f"Не заполнено обязательное поле «{f['label']}». "
                                                   "Задай сотруднику уточняющий вопрос.")
            if value not in (None, "") and f["type"] == "enum" and value not in (f.get("options") or []):
                return error_body("invalid_input", f"Поле «{f['label']}»: допустимы "
                                                   f"{', '.join(f.get('options') or [])}. Уточни у сотрудника.")
        draft = await drafts.create(
            session_id=self.ctx.session_id, employee_id=self.ctx.employee_id,
            type_id=ttype["type_id"], type_name=ttype["name"], title=args["title"].strip(),
            description=args["description"].strip(), fields=values, field_labels=labels,
            trace_id=self.trace_id,
        )
        self.state.draft = drafts.public_view(draft)
        with self.tracer.step("await_confirmation", input={"action_id": draft["action_id"]}) as wait:
            wait.update(output={"status": "pending", "expires_at": draft["expires_at"]})
        obs.update(metadata={"intercepted": True, "action_id": draft["action_id"]})
        return {"status": "draft_created", "action_id": draft["action_id"],
                "note": "Обращение ещё не создано: сотрудник должен нажать кнопку «Создать»."}

    # --- история -----------------------------------------------------------
    async def _remember(self, role: str, content: dict) -> None:
        try:
            await self.history.append(self.ctx.session_id, self.ctx.employee_id, role, content, self.trace_id)
        except Exception:  # noqa: BLE001
            log.warning("history append failed", exc_info=True)

    async def _load_history(self) -> list[dict]:
        try:
            messages = await self.history.load(self.ctx.session_id, self.settings.history_limit)
        except Exception:  # noqa: BLE001
            log.warning("history load failed", exc_info=True)
            return []
        return [{"role": m.role, "content": m.content.get("text", "")} for m in messages
                if m.content.get("text")]

    # --- цикл --------------------------------------------------------------
    async def _loop(self, text: str) -> str:
        items: list[dict] = await self._load_history()
        items.append({"role": "user", "content": text})
        await self._remember("user", {"text": text})
        tools = registry.model_tools()
        for step in range(self.settings.agent_max_steps):
            instructions = prompts.build_instructions(self.ctx.employee_id)
            with self.tracer.step(f"model:agent-step-{step + 1}",
                                  input={"step": step + 1, "items": len(items)},
                                  metadata={"model": self.settings.openai_model}) as obs:
                started = time.monotonic()
                try:
                    response = await openai_client.respond(
                        instructions=instructions, input=items, tools=tools,
                        name=f"agent-step-{step + 1}",
                    )
                except Exception as exc:  # noqa: BLE001 — сбой модели → заготовленное сообщение
                    obs.error(f"model error: {type(exc).__name__}")
                    raise ModelUnavailable(type(exc).__name__) from exc
                obs.update(output=_model_summary(response), metadata={
                    "model": self.settings.openai_model,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "usage": _usage(response)})
            output = list(getattr(response, "output", None) or [])
            calls = [o for o in output if getattr(o, "type", None) == "function_call"]
            if not calls:
                return (getattr(response, "output_text", "") or "").strip()
            items.extend(_as_input_item(o) for o in output)
            for call in calls:
                result = await self._handle_call(call)
                items.append({"type": "function_call_output", "call_id": call.call_id,
                              "output": json.dumps(result, ensure_ascii=False)})
            if self.state.draft is not None:
                return prompts.DRAFT_READY
        return prompts.STEP_LIMIT

    def _finalize(self, text: str) -> tuple[str, list[dict]]:
        cited: list[int] = []
        for match in CITATION_RE.finditer(text):
            for num in match.group(1).split(","):
                ref = int(num.strip())
                if ref in self.state.fragments and ref not in cited:
                    cited.append(ref)
        clean = CITATION_RE.sub("", text).strip()
        sources: list[dict] = []
        seen: set[str] = set()
        for ref in cited:
            frag = self.state.fragments[ref]
            if frag.get("url") in seen:
                continue
            seen.add(frag.get("url"))
            sources.append({"title": frag.get("title"), "url": frag.get("url"),
                            "updated_at": (frag.get("updated_at") or "")[:10]})
        only_empty_search = (
            self.state.search_calls > 0
            and self.state.search_hits == 0
            and self.state.other_calls == 0
            and self.state.draft is None
        )
        if only_empty_search:
            return prompts.REGULATION_NOT_FOUND, []
        return clean or prompts.STEP_LIMIT, sources

    async def run(self, text: str) -> AgentResult:
        with self.tracer.trace(
            "chat", session_id=self.ctx.session_id, user_id=self.ctx.employee_id, input=text,
            metadata={"prompt_version": prompts.PROMPT_VERSION, "model": self.settings.openai_model,
                      "session_id": self.ctx.session_id},
        ) as trace:
            self.trace_id = trace.trace_id
            try:
                async with asyncio.timeout(self.settings.answer_timeout_s):
                    raw = await self._loop(text)
                answer, sources = self._finalize(raw)
            except TimeoutError:
                trace.error("answer timeout")
                answer, sources = prompts.MODEL_UNAVAILABLE, []
            except ModelUnavailable as exc:
                trace.error(f"model unavailable: {exc}")
                answer, sources = prompts.MODEL_UNAVAILABLE, []
            trace.update(output={"text": answer, "sources": sources, "draft": self.state.draft,
                                 "tools": [c["name"] for c in self.state.calls]})
        await self._remember("assistant", {"text": answer, "sources": sources})
        return AgentResult(trace_id=self.trace_id or "", text=answer, sources=sources,
                           draft=self.state.draft, tool_calls=self.state.calls)


def _usage(response: Any) -> dict | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    try:
        return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens}
    except AttributeError:
        return None


def _model_summary(response: Any) -> dict:
    output = list(getattr(response, "output", None) or [])
    calls = [getattr(o, "name", "") for o in output if getattr(o, "type", None) == "function_call"]
    if calls:
        return {"tool_calls": calls}
    return {"text": (getattr(response, "output_text", "") or "")[:2000]}


def _shorten(result: Any, limit: int = 4000) -> Any:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= limit:
        return result
    return {"truncated": text[:limit]}


async def run_agent(session_id: str, employee_id: str, text: str) -> AgentResult:
    return await Agent(session_id, employee_id).run(text)
