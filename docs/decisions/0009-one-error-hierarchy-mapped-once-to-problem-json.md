# 0009 — One error hierarchy, mapped once to problem+json

## Context

Errors were raised in several layers — the store, the turn, the effect path — and each route caught the ones
it knew and built its own `application/problem+json` response. A new error meant a new `except` in every
route that could meet it, and an exception no route knew was answered by the framework as plain text.

## Decision

Every exception defined under `backend/` is an `AppError` (`backend/core/errors.py`) of exactly one of eight
categories. An error carries a kebab-case `code`, a `title`, a `message` and `details`. The category alone
decides the HTTP status, in one table, `STATUS` in `backend/api/problems.py`:

| Category | Status |
|---|---|
| `NotFound` | 404 |
| `Conflict` | 409 |
| `Invalid` | 422 |
| `Denied` | 403 |
| `Unavailable` | 503 with `Retry-After` |
| `Misconfigured` | 503 |
| `CallFailed` | 502 |
| `Internal` | 500, the server's plain text |

`problems.install` registers one handler for every category except `Internal`, and two for the framework's
own errors. Routes raise and never catch. An `Internal` error and an exception that is not an `AppError`
have no handler: the server answers its plain-text `500` and logs the traceback, as before.

## Consequences

- Adding an error is one class in the module that raises it; no route changes
  (`../logging-and-errors.md`).
- An unknown exception keeps the server's plain-text `500`, which carries nothing from the exception. A
  problem document for it was considered and left out, because it would change a response clients already
  receive.
- `Unavailable` and `Misconfigured` share a status and differ in one header: retrying helps only the first.
- `tests/architecture/test_error_model.py` fails on an exception under `backend/` that is not an `AppError`, on a class in
  two categories or none, on a duplicate class name, and on a code that is not kebab-case.
- Several errors never reach HTTP: they are caught and handed to the model as values (`ToolError`,
  `RefundDenied`, `IncidentDenied`) or handled by the loop (`LLMUnavailable`). Their category still gives
  them a status if one ever escapes: an escaped `Denied` would be answered `403`, a `CallFailed` `502` and an
  `Invalid` `422`, where the routes written by hand answered the plain `500`. No path reaches this today.

## Rejected

Translating errors in each route; and one exception class with a status field, which lets any layer pick an
HTTP status.
