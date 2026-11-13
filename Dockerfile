# Один образ для всех сервисов стенда; команду запуска задаёт docker-compose.yml
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Сначала только описания пакетов — слой зависимостей кэшируется
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/pyproject.toml
COPY services/tg_bot/pyproject.toml services/tg_bot/pyproject.toml
COPY services/orchestrator/pyproject.toml services/orchestrator/pyproject.toml
COPY services/search/pyproject.toml services/search/pyproject.toml
COPY services/mcp_systems/pyproject.toml services/mcp_systems/pyproject.toml
COPY services/recommender/pyproject.toml services/recommender/pyproject.toml
COPY services/mock_systems/pyproject.toml services/mock_systems/pyproject.toml
RUN mkdir -p packages/common/common && touch packages/common/common/__init__.py \
    && uv sync --frozen --all-packages --no-install-workspace

COPY packages ./packages
COPY services ./services
COPY seed ./seed
COPY eval ./eval
COPY tests ./tests
COPY pytest.ini ./
RUN uv sync --frozen --all-packages
