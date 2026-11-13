"""Настройки стенда из переменных окружения (принцип V: модель, адреса, пороги — только отсюда)."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "seed").is_dir():
            return parent
    return Path.cwd()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = ""
    telegram_proxy: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_base_url: str = ""
    openai_reasoning_effort: str = "low"

    jwt_secret: str = "insecure-dev-secret-change-me-0123456789abcdef"

    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    database_url: str = "postgresql+asyncpg://stand:stand@localhost:5432/stand"
    redis_url: str = "redis://localhost:6379/0"

    orchestrator_url: str = "http://orchestrator:8000"
    search_url: str = "http://search:8010"
    mcp_url: str = "http://mcp-systems:8020"
    recommender_url: str = "http://recommender:8030"
    mock_url: str = "http://mock-systems:8040"
    bot_url: str = "http://tg-bot:8443"
    mock_public_url: str = "http://localhost:8040"

    agent_max_steps: int = 6
    tool_timeout_s: float = 8.0
    answer_timeout_s: float = 18.0
    history_limit: int = 12
    draft_ttl_s: int = 600
    index_interval_s: int = 60
    search_min_similarity: float = 0.3
    stand_timezone: str = "Europe/Moscow"

    seed_dir: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.stand_timezone)

    @property
    def seed_path(self) -> Path:
        return Path(self.seed_dir) if self.seed_dir else _repo_root() / "seed"

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()


def _convert(raw: str, target: type, default):
    if target is int or isinstance(default, int):
        return int(raw)
    if target is float or isinstance(default, float):
        return float(raw)
    return raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    values = {}
    for f in fields(Settings):
        raw = os.environ.get(f.name.upper())
        if raw is None or raw == "":
            continue
        values[f.name] = _convert(raw, type(f.default), f.default)
    return Settings(**values)


def reset_settings() -> None:
    """Сбросить кэш настроек (для тестов после изменения окружения)."""
    get_settings.cache_clear()
