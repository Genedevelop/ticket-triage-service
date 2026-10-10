# 0010 — One JSON event logger

Source: `design.md` §11.

## Context

A decision is reconstructed from the database, not from logs: every model call and tool call is a row in
`steps`. Logs have a narrower job — to lead to those rows by id and to show timing and outcome — and they
must not become a second copy of customer text.

## Decision

`backend/core/logging.py` is the only module that touches the standard `logging` module or prints a diagnostic.
It offers three functions:

- `configure(level)` — called once in `create_app`; an unknown level raises `UnknownLogLevel`;
- `bound(**ids)` — a context manager that carries ids on every line logged inside it, through a `ContextVar`;
- `event(name, *, outcome, duration_ms=None, payload=None, **fields)` — writes one JSON line to standard
  output.

Every line has the same keys: `ts`, `event`, `request_id`, `conversation_id`, `turn_id`, `action_id`,
`duration_ms`, `outcome`. There are six event names. `payload` — message text, tool arguments and results —
is written only at `DEBUG`.

## Consequences

- Call sites never pass ids by hand: the middleware binds the request id and `triage_turn._run` binds the
  conversation and turn ids.
- `tests/architecture/test_logging_rule.py` fails on `import logging` anywhere else under `backend/`, and on `print()`
  anywhere else except under `backend/cli`, where a command prints its output.
- `tests/integration/test_log_events.py` pins the event names, the id keys and the absence of payloads at the default
  level.
- There is no tracing and no log line for an unhandled 500; the server's own traceback is the record of it.

## Rejected

A logger per module, and a tracing dependency: `design.md` notes that OpenTelemetry's conventions for model
calls were still marked as in development.
