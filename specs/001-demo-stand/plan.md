# Implementation Plan: Демо-стенд ИИ-помощника по внутренним процессам

**Branch**: `001-demo-stand` | **Date**: 2026-11-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-demo-stand/spec.md`

## Summary

Стенд показывает четыре сценария пилота и наблюдаемость в Telegram-боте. Шесть
собственных сервисов на Python в Docker Compose: бот, оркестратор с собственным
циклом агента поверх OpenAI Responses API, сервис поиска с индексатором (PostgreSQL
и pgvector), MCP-серверы систем, сервис рекомендаций и заглушка пяти систем. Права
проверяют заглушки и поиск по короткоживущему JWT, который выпускает бот. Создание
обращения идёт через черновик в Redis и кнопку подтверждения. Все шаги пишутся
в Langfuse. Решения и альтернативы — в [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, aiogram 3, FastMCP 3, OpenAI Python SDK (Responses API), Langfuse Python SDK 5, APScheduler, SQLAlchemy 2 + asyncpg, pgvector, redis-py, PyJWT, jsonschema, httpx

**Storage**: PostgreSQL 17 с pgvector (история, индекс, сигналы, подписки); Redis 7 (роли, токены, черновики); SQLite в памяти (заглушка систем)

**Testing**: pytest, pytest-asyncio, httpx ASGI-клиент, respx; внешние вызовы подменяются

**Target Platform**: Docker Compose на Linux, macOS или Windows (Docker Desktop)

**Project Type**: набор веб-сервисов (монорепозиторий, uv workspace, один образ)

**Performance Goals**: индикатор набора ≤ 5 с, полный ответ ≤ 20 с в 95 % случаев, уведомление ≤ 3 мин после изменения данных

**Constraints**: только синтетические данные; нужен исходящий доступ к Telegram и OpenAI (напрямую или через прокси); Langfuse требует около 4 ГБ памяти; без анонимайзера, фильтров, шлюзов, аудита

**Scale/Scope**: до 10 одновременных пользователей; 4 демо-сотрудника, 11 инструментов агента, 4 сервисные выборки, 4 правила, 15–20 страниц регламентов

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Принцип | Как выполняется в проекте | До | После |
|---|---|---|---|
| I. Права проверяет система | JWT в заголовке; сотрудник не входит в схемы инструментов; проверка в заглушке и SQL-фильтре поиска; сервисные выборки на отдельной точке `/service` | PASS | PASS |
| II. Модель предлагает, код исполняет | Белый список и JSON-схемы в оркестраторе; `sd_create_ticket` превращается в черновик; подтверждение только через `/actions/{id}/confirm`; `action_id` — ключ идемпотентности; лимит шагов и таймауты | PASS | PASS |
| III. Ответ только по источникам | Системный промпт и проверка наличия источников кодом; ссылки и даты добавляет оркестратор из результатов поиска; данные систем не кэшируются | PASS | PASS |
| IV. Рекомендации по правилам | `rules.yaml` и код условий; модель только в `/formulate`, с запасным шаблоном | PASS | PASS |
| V. Границы сервисов | Оркестратор ходит в системы только через MCP; заглушки повторяют контракт раздела 5.5; настройки в `.env`; единый формат ошибок в `common` | PASS | PASS |
| VI. Трассируемость | Трасса на сообщение, сессия на диалог, score от кнопок; сбой SDK перехватывается | PASS | PASS |
| VII. Синтетические данные | `seed/` с вымышленными данными; `.env` в `.gitignore`; предупреждение в `/start` | PASS | PASS |
| VIII. Простота показа | Один образ, один `docker compose up`; `POST /admin/reset`; относительные даты; нет компонентов сверх плана | PASS | PASS |

Отступлений нет, раздел Complexity Tracking не заполняется.

Уточнение по принципу V: поиск по регламентам и инструменты рекомендаций
(`search_regulations`, `recs_list_my`, `recs_set_subscription`) оркестратор вызывает
по REST у сервисов помощника, а не через MCP. Это соответствует разделу 5.5
архитектурного решения: MCP — только для внешних систем.

## Project Structure

### Documentation (this feature)

```text
specs/001-demo-stand/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── agent-tools.md        # 11 инструментов агента и 4 сервисные выборки
│   ├── internal-api.md       # REST между сервисами помощника
│   └── mock-systems-api.md   # REST заглушек и пульт ведущего
├── checklists/requirements.md
└── tasks.md                  # создаётся командой /speckit-tasks
```

### Source Code (repository root)

```text
docker-compose.yml             # 6 сервисов помощника, postgres, redis;
                               # контейнер tests запускается по требованию
docker-compose.langfuse.yml    # Langfuse и его хранилища
Dockerfile                     # один образ, команда запуска задаётся в compose
pyproject.toml                 # uv workspace
.env.example
README.md

packages/common/               # настройки, JWT, ошибки, общие схемы, логирование,
                               # справочник демо-сотрудников
services/
├── tg_bot/                    # aiogram: роли, диалог, кнопки, /notify
├── orchestrator/              # цикл агента, инструменты, черновики, Langfuse
│   ├── agent/                 # цикл, промпты, реестр инструментов и схемы
│   ├── clients/               # MCP, поиск, рекомендации, OpenAI
│   └── api/                   # /chat, /actions, /formulate, /feedback
├── search/                    # /search, индексатор, нарезка, эмбеддинги
├── mcp_systems/               # FastMCP: crm, billing, sd, calendar, service
├── recommender/               # правила, планировщик, сигналы, подписки
└── mock_systems/              # REST пяти систем, /admin, /ui

seed/                          # employees, clients, invoices, tickets,
                               # ticket_types, events (yaml), pages/*.md
eval/                          # dataset.yaml, run_eval.py, upload_dataset.py
tests/
├── contract/                  # схемы инструментов, выборок, REST
├── integration/               # сценарии историй, «чужой доступ», подтверждение
└── unit/                      # нарезка, RRF, правила, цикл агента
```

**Structure Decision**: монорепозиторий с каталогом на сервис и общим пакетом
`common`. Один Docker-образ со всеми сервисами; в `docker-compose.yml` у каждого
контейнера своя команда запуска. Границы сервисов — сетевые (HTTP и MCP), импорт
кода между сервисами запрещён, общий код только в `packages/common`.

**Справочник сотрудников**: в стенде нет корпоративного каталога, поэтому состав
демо-сотрудников, их группы и руководителей читает из `seed/employees.yaml` общий
модуль `common.directory`. Им пользуются бот (выпуск токена) и сервис рекомендаций
(выбор получателя сигнала о встрече). Решения о доступе по справочнику и по полям
токена не принимаются: заглушка берёт из токена только `sub` и `role`, а
подчинённость определяет по своей таблице сотрудников; поле `groups` токена
использует только поиск.

## Порядок реализации

Этапы соответствуют приоритетам историй; после каждого стенд показывает на один
сценарий больше.

1. **Основание**: каркас, `common`, compose, заглушка систем с данными и сбросом,
   бот с выбором роли, минимальный оркестратор без инструментов, трасса в Langfuse.
2. **История 1 (P1)**: индексатор, поиск, `search_regulations`, ответ со ссылкой
   и датой, отказ без источника.
3. **История 2 (P2)**: MCP-серверы четырёх систем, восемь читающих инструментов,
   различение `forbidden`, `not_found`, `unavailable`.
4. **История 3 (P3)**: `sd_list_ticket_types`, черновик, кнопки, подтверждение,
   идемпотентность.
5. **История 4 (P4)**: сервисные выборки, правила, планировщик, `/notify`,
   подписки, `/digest`.
6. **История 5 (P5)**: оценки, полнота трасс, тестовый набор, прогон, README.

Контрактные тесты и тесты «чужой доступ» пишутся в том же этапе, что и инструмент.

## Complexity Tracking

Нарушений конституции нет.
