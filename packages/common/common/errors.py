"""Единый формат ошибок: {error: {code, message}} с кодами forbidden, not_found, invalid_input, unavailable."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

ErrorCode = Literal["forbidden", "not_found", "invalid_input", "unavailable"]

CODES: tuple[str, ...] = ("forbidden", "not_found", "invalid_input", "unavailable")

DEFAULT_MESSAGES: dict[str, str] = {
    "forbidden": "Нет доступа к запрошенным данным.",
    "not_found": "Объект не найден.",
    "invalid_input": "Неверные параметры запроса.",
    "unavailable": "Система временно недоступна.",
}

HTTP_STATUS: dict[str, int] = {
    "invalid_input": 400,
    "forbidden": 403,
    "not_found": 404,
    "unavailable": 503,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str | None = None, status: int | None = None):
        if code not in CODES:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.message = message or DEFAULT_MESSAGES[code]
        self.status = status or HTTP_STATUS[code]
        super().__init__(f"{code}: {self.message}")

    def body(self) -> dict:
        return error_body(self.code, self.message)


def error_body(code: str, message: str | None = None) -> dict:
    return {"error": {"code": code, "message": message or DEFAULT_MESSAGES[code]}}


def code_from_status(status: int) -> str:
    if status == 400 or status == 422:
        return "invalid_input"
    if status in (401, 403):
        return "forbidden"
    if status == 404:
        return "not_found"
    return "unavailable"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = []
        for err in exc.errors()[:3]:
            loc = ".".join(str(x) for x in err.get("loc", []) if x not in ("body", "query", "path"))
            parts.append(f"{loc}: {err.get('msg', '')}".strip(": "))
        message = "Неверные параметры запроса" + (f" ({'; '.join(parts)})" if parts else "") + "."
        return JSONResponse(error_body("invalid_input", message), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = code_from_status(exc.status_code)
        message = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else None
        return JSONResponse(error_body(code, message), status_code=exc.status_code)
