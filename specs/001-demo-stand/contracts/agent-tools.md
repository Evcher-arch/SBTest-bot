# Contract: инструменты агента и сервисные выборки

Основание — раздел 5.5 архитектурного решения. Имена, входы и поля ответа менять
нельзя: по ним заглушка заменяется реальной системой.

## Общие правила

- Вход каждого инструмента проверяется по JSON-схеме (`additionalProperties: false`)
  в оркестраторе до вызова и в MCP-сервере при вызове.
- Сотрудник определяется только по JWT в заголовке `Authorization: Bearer`.
  Параметра «от чьего имени» нет ни у одного инструмента.
- Списки: не больше 20 записей, ответ `{items: [...], has_more: bool}`.
- Ошибка: `{error: {code, message}}`, где `code` — `forbidden`, `not_found`,
  `invalid_input`, `unavailable`. `message` — на русском, пригоден для показа.
- Даты — ISO 8601; суммы — число в рублях; `url` — ссылка на карточку в заглушке.

## Инструменты агента

| Инструмент | Где | Вход | Ответ |
|---|---|---|---|
| `search_regulations` | search (REST) | `query`: строка, обяз.; `top_k`: 1–8, по умолч. 5 | `items[]`: `title`, `section`, `url`, `updated_at`, `text` |
| `crm_find_client` | MCP `/crm` | ровно одно из `name` (от 2 символов), `inn` | `items[]`: `client_id`, `name`, `manager`, `url` |
| `billing_list_invoices` | MCP `/billing` | `client_id`: обяз.; `status`: `issued`, `partially_paid`, `paid`, `overdue`, `cancelled`; `period`: `{from, to}` | `items[]`: `number`, `date`, `due_date`, `amount`, `status`, `overdue_days`, `url` |
| `billing_get_invoice` | MCP `/billing` | `number`: обяз. | поля счёта и `payments[]`: `date`, `amount` |
| `sd_list_my_tickets` | MCP `/sd` | `role`: `author` или `assignee`, обяз.; `status`; `employee`: логин подчинённого | `items[]`: `number`, `type`, `title`, `status`, `due_at`, `url` |
| `sd_get_ticket` | MCP `/sd` | `number`: обяз. | поля обращения, `assignee`, `history[]`: `at`, `actor`, `action` |
| `sd_list_ticket_types` | MCP `/sd` | нет | `items[]`: `type_id`, `name`, `fields[]`: `name`, `label`, `required`, `type`, `options` |
| `sd_create_ticket` | MCP `/sd` | `type_id`, `title`, `description`: обяз.; `fields`: объект; `idempotency_key`: обяз. | `number`, `status`, `url` |
| `calendar_list_my_events` | MCP `/calendar` | `from`, `to`: дата-время, обяз.; интервал до 31 дня | `items[]`: `subject`, `start`, `end`, `organizer`, `attendees[]` |
| `recs_list_my` | recommender (REST) | `date`: дата, по умолч. сегодня | `items[]`: `rule`, `text`, `object`, `url` |
| `recs_set_subscription` | recommender (REST) | `rule`: одно из четырёх правил; `enabled`: bool | `items[]`: `rule`, `enabled` по всем правилам |

### Особые правила

- **`sd_create_ticket`** — единственный изменяющий инструмент. Модели он
  показывается без поля `idempotency_key`. Оркестратор не исполняет вызов,
  а создаёт черновик; реальный вызов делает только обработчик подтверждения,
  подставляя `idempotency_key` = `action_id`. Повтор с тем же ключом возвращает
  ранее созданное обращение.
- **`sd_list_my_tickets.employee`** — фильтр, а не смена личности. Если указанный
  сотрудник не подчинён вызывающему, ответ `forbidden`.
- **`crm_find_client`** возвращает только клиентов, доступных вызывающему. Клиент
  чужого портфеля в поиске не виден (пустой список), прямой запрос его счёта даёт
  `forbidden`.
- **`calendar_list_my_events`** всегда возвращает встречи владельца токена.
- **`search_regulations`** применяет фильтр по `groups` из токена. Недоступная
  страница неотличима от несуществующей.
- **`recs_list_my`** при каждом вызове заново проверяет все правила по текущим
  данным систем и возвращает сигналы, получатель которых — владелец токена. Сигналы
  отключённых им правил не возвращаются. Вызов ничего не записывает и не отправляет:
  таблица отправленных сигналов не меняется, поэтому сводка не отменяет и не
  дублирует уведомление по расписанию. `text` — текст по шаблону правила, без
  обращения к модели. Условия считаются относительно `date`.
- **`recs_set_subscription`** выполняется без кнопки подтверждения: он меняет
  только настройку самого сотрудника в сервисе помощника и обратим. Это не
  изменяющее действие в системах компании (FR-019).

## Сервисные выборки

Точка MCP `/service`. Доступны только с токеном `role: recs-service`; любой другой
токен получает `forbidden`. Оркестратор к этой точке не подключается, в реестре
инструментов агента этих имён нет.

| Выборка | Вход | Ответ |
|---|---|---|
| `billing_select_invoices` | `status`: `overdue` или `due_soon`, обяз.; `due_before`: дата | `items[]`: `number`, `client_id`, `due_date`, `amount`, `overdue_days`, `url` |
| `crm_select_client_managers` | `client_ids[]`: до 100 | `items[]`: `client_id`, `name`, `manager` |
| `sd_select_stale_tickets` | `idle_days`: целое ≥ 1; `due_within`: часы | `items[]`: `number`, `title`, `assignee`, `status`, `due_at`, `url` |
| `calendar_select_events` | `date`: обяз.; `users[]`: логины | `items[]`: `user`, `subject`, `start`, `organizer`, `attendees[]` |

Для выборок лимит списка — 200 записей.

## Обязательные контрактные тесты

Для каждого инструмента и выборки:

1. Корректный вход возвращает ответ, соответствующий схеме.
2. Лишнее или неверное поле даёт `invalid_input`.
3. Запрос без токена или с просроченным токеном даёт `forbidden`.
4. Тест «чужой доступ»: Иванов запрашивает объект Петровой и получает `forbidden`
   либо пустой список — как описано выше.
5. Для выборок: токен сотрудника даёт `forbidden`.
6. Для `sd_create_ticket`: два вызова с одним ключом дают один номер.
