"""HTML-карточки объектов, на которые ведут ссылки из ответов помощника (FR-031).

Демонстрационное упрощение: карточки открыты без авторизации.
"""

from __future__ import annotations

import html

from common.errors import ApiError
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from ..models import Store, get_store
from .common import client_view, invoice_view, ticket_view

router = APIRouter(prefix="/ui", tags=["Карточки"], include_in_schema=False)

STATUS_RU = {
    "issued": "выставлен", "partially_paid": "оплачен частично", "paid": "оплачен",
    "cancelled": "отменён", "overdue": "просрочен", "new": "новое", "in_progress": "в работе",
    "waiting": "ожидание", "resolved": "решено", "closed": "закрыто",
}

STYLE = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;margin:24px auto;
padding:0 16px;color:#1f2328;background:#fff}
h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:1.4em}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #d8dee4;padding:6px 8px;
text-align:left;vertical-align:top}th{width:35%;color:#59636e;font-weight:500}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;background:#eef1f4}
.note{color:#59636e;font-size:.85rem;margin-top:2em}pre{white-space:pre-wrap;font-family:inherit}
@media (prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}td,th{border-color:#30363d}
th{color:#9198a1}.badge{background:#21262d}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    doc = (
        f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>"
        f"{body}<p class='note'>Демо-стенд: вымышленные данные заглушки систем.</p></body></html>"
    )
    return HTMLResponse(doc)


def _table(rows: list[tuple[str, object]]) -> str:
    cells = "".join(
        f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v if v is not None else '—'))}</td></tr>"
        for k, v in rows
    )
    return f"<table>{cells}</table>"


def _money(value: float) -> str:
    return f"{value:,.2f} ₽".replace(",", " ")


@router.get("/clients/{client_id}")
async def client_card(client_id: str, store: Store = Depends(get_store)) -> HTMLResponse:
    client = store.client(client_id)
    if client is None:
        raise ApiError("not_found")
    view = client_view(store, client)
    rows = [("Клиент", view["name"]), ("ИНН", view["inn"]), ("Ответственный", view["manager"]["name"])]
    invoices = "".join(
        f"<tr><td><a href='{i['url']}'>{html.escape(i['number'])}</a></td><td>{_money(i['amount'])}</td>"
        f"<td>{i['due_date']}</td><td>{STATUS_RU.get(i['status'], i['status'])}</td></tr>"
        for i in (invoice_view(store, inv) for inv in store.invoices(client_id))
    )
    return _page(view["name"], f"<h1>{html.escape(view['name'])}</h1>{_table(rows)}"
                 f"<h2>Счета</h2><table>{invoices}</table>")


@router.get("/invoices/{number}")
async def invoice_card(number: str, store: Store = Depends(get_store)) -> HTMLResponse:
    inv = store.invoice(number)
    if inv is None:
        raise ApiError("not_found")
    v = invoice_view(store, inv, full=True)
    rows = [
        ("Клиент", v["client_name"]), ("Дата счёта", v["date"]), ("Срок оплаты", v["due_date"]),
        ("Сумма", _money(v["amount"])), ("Оплачено", _money(v["paid_amount"])),
        ("Статус", STATUS_RU.get(v["status"], v["status"])), ("Дней просрочки", v["overdue_days"]),
    ]
    pays = "".join(f"<tr><td>{p['date']}</td><td>{_money(p['amount'])}</td></tr>" for p in v["payments"])
    return _page(f"Счёт {number}", f"<h1>Счёт {html.escape(number)}</h1>{_table(rows)}"
                 f"<h2>Оплаты</h2><table>{pays or '<tr><td>Оплат нет</td></tr>'}</table>")


@router.get("/tickets/{number}")
async def ticket_card(number: str, store: Store = Depends(get_store)) -> HTMLResponse:
    ticket = store.ticket(number)
    if ticket is None:
        raise ApiError("not_found")
    v = ticket_view(store, ticket, full=True)
    rows = [
        ("Тип", v["type"]), ("Статус", STATUS_RU.get(v["status"], v["status"])),
        ("Автор", v["author"]["name"]), ("Исполнитель", (v["assignee"] or {}).get("name")),
        ("Создано", v["created_at"]), ("Срок", v["due_at"]),
    ] + [(f["label"], f["value"]) for f in v["fields"]]
    hist = "".join(
        f"<tr><td>{html.escape(h['at'])}</td><td>{html.escape(h['actor'])}</td>"
        f"<td>{html.escape(h['action'])}</td></tr>" for h in v["history"]
    )
    return _page(f"{number}: {v['title']}", f"<h1>{html.escape(number)}: {html.escape(v['title'])}</h1>"
                 f"{_table(rows)}<h2>Описание</h2><pre>{html.escape(v['description'])}</pre>"
                 f"<h2>История</h2><table>{hist}</table>")


@router.get("/pages/{page_id}")
async def page_card(page_id: str, store: Store = Depends(get_store)) -> HTMLResponse:
    page = store.page(page_id)
    if page is None:
        raise ApiError("not_found")
    meta = (f"<p><span class='badge'>{html.escape(page['space'])}</span> "
            f"Обновлено: {html.escape(page['updated_at'][:10])} · версия {page['version']}</p>")
    return _page(page["title"], f"{meta}<pre>{html.escape(page['body'])}</pre>")
