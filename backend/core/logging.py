import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from backend.core.errors import Misconfigured

LOGGER = "triage"
# the ids every line carries; one that does not apply is null
IDS = ("request_id", "conversation_id", "turn_id", "action_id")

_logger = logging.getLogger(LOGGER)
_bound: ContextVar[dict[str, Any]] = ContextVar("triage_log_ids", default={})


class UnknownLogLevel(Misconfigured):
    code = "unknown-log-level"
    title = "Unknown log level"


class _CurrentStdoutHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        print(record.getMessage(), file=sys.stdout, flush=True)


def configure(level: str) -> None:
    # Step 1: Set the level, refusing a name the standard logger does not know with the logger's own words
    try:
        _logger.setLevel(level)
    except ValueError as error:
        raise UnknownLogLevel(str(error)) from error
    # Step 2: Attach the standard-output handler once, however often the service is built in one process
    if not _logger.handlers:
        _logger.addHandler(_CurrentStdoutHandler())


@contextmanager
def bound(**ids: Any) -> Iterator[None]:
    token = _bound.set({**_bound.get(), **ids})
    try:
        yield
    finally:
        _bound.reset(token)


def event(name: str, *, outcome: str, duration_ms: int | None = None, payload: Any = None, **fields: Any) -> None:
    # Step 1: Skip the work when the level is above INFO
    if not _logger.isEnabledFor(logging.INFO):
        return
    # Step 2: Build the line with every id key present, null where none is bound
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": name,
        **{key: None for key in IDS},
        **_bound.get(),
        "duration_ms": duration_ms,
        "outcome": outcome,
        **fields,
    }
    # Step 3: Add the payload only at DEBUG, because it carries bodies, arguments and results
    if payload is not None and _logger.isEnabledFor(logging.DEBUG):
        line["payload"] = payload
    # Step 4: Write the line as one JSON object
    _logger.info(json.dumps(line, ensure_ascii=False, default=str))
