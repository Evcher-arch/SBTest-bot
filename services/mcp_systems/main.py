"""Процесс MCP-серверов (порт 8020): FastMCP 3, Streamable HTTP.

Точки: /crm/mcp, /billing/mcp, /sd/mcp, /calendar/mcp (инструменты агента) и /service/mcp
(сервисные выборки). Режим без сессий (stateless) и JSON-ответы — каждый вызов независим.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from common.logging import setup_logging
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import billing, calendar, crm, sd, service

BUILDERS = {"crm": crm.build, "billing": billing.build, "sd": sd.build,
            "calendar": calendar.build, "service": service.build}


def create_app() -> Starlette:
    servers = {name: build() for name, build in BUILDERS.items()}
    apps = {name: srv.http_app(path="/mcp", stateless_http=True, json_response=True)
            for name, srv in servers.items()}

    @asynccontextmanager
    async def lifespan(_: Starlette):
        setup_logging("mcp-systems")
        async with AsyncExitStack() as stack:
            for sub in apps.values():
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "mcp-systems", "endpoints": list(apps)})

    routes = [Route("/health", health)] + [Mount(f"/{name}", app=sub) for name, sub in apps.items()]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.servers = servers
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8020)
