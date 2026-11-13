"""Чтение и проверка eval/dataset.yaml (общая часть upload_dataset.py и run_eval.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DATASET_PATH = Path(__file__).with_name("dataset.yaml")
DATASET_NAME = "demo-stand-eval"

CATEGORIES = ("process", "status", "action", "no_answer", "foreign_data", "injection")
DETERMINISTIC_ONLY = ("foreign_data", "injection")
EMPLOYEES = ("ivanov", "petrova", "sidorov", "kuznetsova")
REQUIRED = ("id", "category", "employee_id", "question", "expected_answer", "expected_tools",
            "must_contain", "must_not_contain", "must_not_create_ticket")


def load_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    seen: set[str] = set()
    for item in items:
        missing = [k for k in REQUIRED if k not in item]
        if missing:
            raise ValueError(f"{item.get('id', '?')}: нет полей {missing}")
        if item["id"] in seen:
            raise ValueError(f"повторяющийся id {item['id']}")
        seen.add(item["id"])
        if item["category"] not in CATEGORIES:
            raise ValueError(f"{item['id']}: неизвестная категория {item['category']}")
        if item["employee_id"] not in EMPLOYEES:
            raise ValueError(f"{item['id']}: неизвестный сотрудник {item['employee_id']}")
    for category in DETERMINISTIC_ONLY:
        count = sum(1 for i in items if i["category"] == category)
        if count < 4:
            raise ValueError(f"в категории {category} {count} вопросов, нужно не меньше 4")
    return items
