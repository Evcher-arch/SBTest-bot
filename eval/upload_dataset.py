"""Загрузка eval/dataset.yaml в набор данных Langfuse.

Запуск: docker compose run --rm tests python eval/upload_dataset.py

Повторный запуск не создаёт дублей: id элемента набора совпадает с id вопроса,
и Langfuse обновляет существующий элемент вместо создания нового.
"""

from __future__ import annotations

import sys

from common.settings import get_settings
from dataset_io import DATASET_NAME, load_dataset


def main() -> int:
    s = get_settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        print("LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY не заданы — загрузка невозможна.")
        return 1
    from langfuse import Langfuse

    items = load_dataset()
    client = Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key,
                      base_url=s.langfuse_host)
    try:
        client.get_dataset(DATASET_NAME, fetch_items_page_size=1)
    except Exception:  # noqa: BLE001 — набора ещё нет
        client.create_dataset(
            name=DATASET_NAME,
            description="Тестовый набор демо-стенда ИИ-помощника (eval/dataset.yaml)",
            metadata={"source": "eval/dataset.yaml"},
        )
    for item in items:
        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=f"{DATASET_NAME}-{item['id']}",
            input={"employee_id": item["employee_id"], "question": item["question"]},
            expected_output={"answer": item["expected_answer"],
                             "tools": item["expected_tools"],
                             "must_contain": item["must_contain"],
                             "must_not_contain": item["must_not_contain"]},
            metadata={"id": item["id"], "category": item["category"],
                      "must_not_create_ticket": item["must_not_create_ticket"],
                      "must_not_draft": item.get("must_not_draft", False)},
        )
    client.flush()
    print(f"Набор «{DATASET_NAME}»: загружено {len(items)} вопросов в {s.langfuse_host}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
