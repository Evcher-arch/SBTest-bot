"""JSON-схемы входа инструментов агента и сервисных выборок (contracts/agent-tools.md).

Один источник для проверки в оркестраторе (до вызова) и в MCP-серверах (при вызове).
Параметра «от чьего имени» нет ни у одной схемы: сотрудник берётся только из токена.
"""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ApiError

DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
DATETIME = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"}
INVOICE_STATUSES = ["issued", "partially_paid", "paid", "overdue", "cancelled"]
TICKET_STATUSES = ["new", "in_progress", "waiting", "resolved", "closed"]
RULES = ["invoice_overdue", "invoice_due_soon", "meeting_tomorrow", "ticket_stale"]


def _obj(properties: dict, required: list[str] | None = None, **extra: Any) -> dict:
    schema = {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
    schema.update(extra)
    return schema


AGENT_TOOLS: dict[str, dict] = {
    "search_regulations": {
        "description": (
            "Поиск по регламентам и внутренним инструкциям компании. Возвращает фрагменты "
            "страниц, доступных текущему сотруднику, с заголовком, разделом, ссылкой и датой обновления."
        ),
        "schema": _obj(
            {
                "query": {"type": "string", "minLength": 2, "maxLength": 500,
                          "description": "Поисковый запрос на русском языке"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 8,
                          "description": "Сколько фрагментов вернуть (по умолчанию 5)"},
            },
            ["query"],
        ),
    },
    "crm_find_client": {
        "description": (
            "Найти клиента в CRM по части названия или по ИНН. Ровно одно из полей. "
            "Возвращает только клиентов, доступных текущему сотруднику."
        ),
        "schema": _obj(
            {
                "name": {"type": "string", "minLength": 2, "description": "Часть названия клиента"},
                "inn": {"type": "string", "pattern": r"^(\d{10}|\d{12})$", "description": "ИНН"},
            },
            oneOf=[{"required": ["name"]}, {"required": ["inn"]}],
        ),
    },
    "billing_list_invoices": {
        "description": "Список счетов клиента: сумма, срок оплаты, статус, дни просрочки, ссылка.",
        "schema": _obj(
            {
                "client_id": {"type": "string", "minLength": 1, "description": "Идентификатор клиента из CRM"},
                "status": {"type": "string", "enum": INVOICE_STATUSES},
                "period": _obj({"from": DATE, "to": DATE}),
            },
            ["client_id"],
        ),
    },
    "billing_get_invoice": {
        "description": "Счёт по номеру (например, СЧ-2026-0142) с оплатами.",
        "schema": _obj({"number": {"type": "string", "minLength": 1}}, ["number"]),
    },
    "sd_list_my_tickets": {
        "description": (
            "Обращения в Service-Desk, где текущий сотрудник автор (role=author) или исполнитель "
            "(role=assignee). Руководитель может указать employee — логин подчинённого."
        ),
        "schema": _obj(
            {
                "role": {"type": "string", "enum": ["author", "assignee"]},
                "status": {"type": "string", "enum": TICKET_STATUSES},
                "employee": {"type": "string", "minLength": 1, "description": "Логин подчинённого"},
            },
            ["role"],
        ),
    },
    "sd_get_ticket": {
        "description": "Карточка обращения по номеру (например, SD-1042) с историей.",
        "schema": _obj({"number": {"type": "string", "minLength": 1}}, ["number"]),
    },
    "sd_list_ticket_types": {
        "description": "Типы обращений Service-Desk и их поля с признаком обязательности.",
        "schema": _obj({}),
    },
    "sd_create_ticket": {
        "description": (
            "Подготовить обращение в Service-Desk. Обращение создаётся только после нажатия "
            "сотрудником кнопки «Создать» под черновиком."
        ),
        "schema": _obj(
            {
                "type_id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {"type": "string", "minLength": 1},
                "fields": {"type": "object", "description": "Значения полей типа: {name: value}"},
                "idempotency_key": {"type": "string", "minLength": 8},
            },
            ["type_id", "title", "description", "idempotency_key"],
        ),
        "hidden_from_model": ["idempotency_key"],
    },
    "calendar_list_my_events": {
        "description": "Встречи текущего сотрудника за период (интервал не больше 31 дня).",
        "schema": _obj(
            {"from": {**DATETIME, "description": "Начало, ISO 8601 с часовым поясом"},
             "to": {**DATETIME, "description": "Конец, ISO 8601 с часовым поясом"}},
            ["from", "to"],
        ),
    },
    "recs_list_my": {
        "description": "Актуальные сигналы (рекомендации) текущего сотрудника: что важно сделать.",
        "schema": _obj({"date": {**DATE, "description": "Дата, по умолчанию сегодня"}}),
    },
    "recs_set_subscription": {
        "description": "Включить или отключить для себя правило рекомендаций.",
        "schema": _obj(
            {"rule": {"type": "string", "enum": RULES}, "enabled": {"type": "boolean"}},
            ["rule", "enabled"],
        ),
    },
}

SERVICE_SELECTIONS: dict[str, dict] = {
    "billing_select_invoices": {
        "description": "Счета всех клиентов: просроченные или с подходящим сроком оплаты.",
        "schema": _obj(
            {"status": {"type": "string", "enum": ["overdue", "due_soon"]}, "due_before": DATE},
            ["status"],
        ),
    },
    "crm_select_client_managers": {
        "description": "Ответственные менеджеры по списку клиентов.",
        "schema": _obj(
            {"client_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100}},
            ["client_ids"],
        ),
    },
    "sd_select_stale_tickets": {
        "description": "Обращения без движения или с истекающим сроком.",
        "schema": _obj(
            {"idle_days": {"type": "integer", "minimum": 1},
             "due_within": {"type": "number", "minimum": 0, "description": "часы"}},
            ["idle_days"],
        ),
    },
    "calendar_select_events": {
        "description": "Встречи сотрудников на дату.",
        "schema": _obj(
            {"date": DATE, "users": {"type": "array", "items": {"type": "string"}, "maxItems": 50}},
            ["date"],
        ),
    },
}

_VALIDATORS: dict[str, Draft202012Validator] = {
    name: Draft202012Validator(spec["schema"])
    for name, spec in {**AGENT_TOOLS, **SERVICE_SELECTIONS}.items()
}


def validate_args(name: str, args: Any) -> dict:
    """Проверить параметры по схеме; ошибка → ApiError(invalid_input)."""
    validator = _VALIDATORS.get(name)
    if validator is None:
        raise ApiError("invalid_input", f"Неизвестный инструмент: {name}.")
    if not isinstance(args, dict):
        raise ApiError("invalid_input", "Параметры инструмента должны быть объектом.")
    errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
    if errors:
        err = errors[0]
        where = ".".join(str(p) for p in err.path) or "параметры"
        raise ApiError("invalid_input", f"Неверные параметры {name}: {where} — {err.message}.")
    return args


def model_schema(name: str) -> dict:
    """Схема для модели: без скрытых полей и без oneOf (его проверяет код)."""
    spec = AGENT_TOOLS[name]
    schema = copy.deepcopy(spec["schema"])
    schema.pop("oneOf", None)
    for hidden in spec.get("hidden_from_model", []):
        schema["properties"].pop(hidden, None)
        if hidden in schema["required"]:
            schema["required"].remove(hidden)
    return schema
