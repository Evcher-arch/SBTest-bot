"""Трассировка в Langfuse: трасса на сообщение, вложенные наблюдения на шаги, оценки.

Любая ошибка SDK перехватывается и не влияет на ответ (принцип VI).
Токен сотрудника в трассу не передаётся: в данные трассы попадают только параметры
и результаты инструментов, а заголовки запросов — никогда.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from common.logging import mask_secrets
from common.settings import get_settings

log = logging.getLogger(__name__)


def _scrub(value: Any) -> Any:
    """Удалить из данных трассы всё, похожее на токен или заголовок Authorization."""
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, dict):
        return {k: ("***" if str(k).lower() == "authorization" else _scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


class Observation:
    """Обёртка наблюдения: методы никогда не бросают исключений."""

    def __init__(self, raw: Any = None) -> None:
        self._raw = raw

    def update(self, **kwargs: Any) -> None:
        if self._raw is None:
            return
        try:
            for key in ("input", "output", "metadata"):
                if key in kwargs:
                    kwargs[key] = _scrub(kwargs[key])
            self._raw.update(**kwargs)
        except Exception:  # noqa: BLE001
            log.debug("langfuse update failed", exc_info=True)

    def error(self, message: str) -> None:
        self.update(level="ERROR", status_message=message)


class TraceHandle(Observation):
    def __init__(self, raw: Any, trace_id: str) -> None:
        super().__init__(raw)
        self.trace_id = trace_id


class Tracer:
    def __init__(self, client: Any = None) -> None:
        self._client = client
        self._init_failed = False

    def client(self) -> Any:
        if self._client is not None or self._init_failed:
            return self._client
        s = get_settings()
        if not (s.langfuse_public_key and s.langfuse_secret_key):
            self._init_failed = True
            return None
        try:
            os.environ.setdefault("LANGFUSE_BASE_URL", s.langfuse_host)
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=s.langfuse_public_key,
                secret_key=s.langfuse_secret_key,
                base_url=s.langfuse_host,
                timeout=5,
            )
        except Exception:  # noqa: BLE001
            log.warning("Langfuse недоступен, трассы не пишутся", exc_info=True)
            self._init_failed = True
        return self._client

    @contextmanager
    def _observe(self, name: str, *, as_type: str, input: Any = None,
                 metadata: Any = None, **extra: Any) -> Iterator[Any]:
        client = self.client()
        cm = raw = None
        if client is not None:
            try:
                cm = client.start_as_current_observation(
                    name=name, as_type=as_type, input=_scrub(input), metadata=_scrub(metadata), **extra
                )
                raw = cm.__enter__()
            except Exception:  # noqa: BLE001
                log.debug("langfuse start failed", exc_info=True)
                cm = raw = None
        exc_info: tuple = (None, None, None)
        try:
            yield raw
        except BaseException:
            exc_info = sys.exc_info()
            raise
        finally:
            if cm is not None:
                try:
                    cm.__exit__(*exc_info)
                except Exception:  # noqa: BLE001
                    log.debug("langfuse end failed", exc_info=True)

    @contextmanager
    def trace(self, name: str, *, session_id: str, user_id: str, input: Any = None,
              metadata: Any = None) -> Iterator[TraceHandle]:
        with self._observe(name, as_type="agent", input=input, metadata=metadata) as raw:
            trace_id = None
            propagate = None
            if raw is not None:
                try:
                    trace_id = getattr(raw, "trace_id", None) or self._client.get_current_trace_id()
                    from langfuse import propagate_attributes

                    propagate = propagate_attributes(
                        user_id=user_id, session_id=session_id, trace_name=name
                    )
                    propagate.__enter__()
                except Exception:  # noqa: BLE001
                    propagate = None
            handle = TraceHandle(raw, trace_id or uuid.uuid4().hex)
            try:
                yield handle
            finally:
                if propagate is not None:
                    try:
                        propagate.__exit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass

    @contextmanager
    def step(self, name: str, *, as_type: str = "span", input: Any = None,
             metadata: Any = None) -> Iterator[Observation]:
        with self._observe(name, as_type=as_type, input=input, metadata=metadata) as raw:
            yield Observation(raw)

    def score(self, trace_id: str, value: float, *, name: str = "user_feedback",
              comment: str | None = None) -> bool:
        client = self.client()
        if client is None:
            return False
        try:
            client.create_score(trace_id=trace_id, name=name, value=value,
                                data_type="NUMERIC", comment=comment)
            return True
        except Exception:  # noqa: BLE001
            log.warning("не удалось записать оценку в Langfuse", exc_info=True)
            return False

    def flush(self) -> None:
        client = self.client()
        if client is None:
            return
        try:
            client.flush()
        except Exception:  # noqa: BLE001
            pass


_tracer = Tracer()


def get_tracer() -> Tracer:
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    _tracer = tracer
