"""Настройка логирования с маскированием токенов (принцип I: токен не попадает в логи)."""

from __future__ import annotations

import logging
import re

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_AUTH_RE = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(bearer\s+)?[^\s'\",}]+")
MASK = "***"


def mask_secrets(text: str) -> str:
    text = _AUTH_RE.sub(lambda m: m.group(1) + MASK, text)
    return _JWT_RE.sub(MASK, text)


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — запись с неверными аргументами не должна ронять лог
            return True
        masked = mask_secrets(message)
        if masked != message:
            record.msg = masked
            record.args = None
        return True


def setup_logging(service: str, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(f, SecretFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(f"%(asctime)s {service} %(levelname)s %(name)s: %(message)s")
        )
        handler.addFilter(SecretFilter())
        root.handlers = [handler]
    root.setLevel(level)
    # httpx пишет URL запросов; заголовки не пишет, но держим уровень WARNING
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    for noisy in ("mcp", "fastmcp", "apscheduler.executors", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
