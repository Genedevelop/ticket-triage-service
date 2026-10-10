# Code style

The rules the code under `backend/`, in `main.py` and under `evals/` follows, each with an example from this
repository and the way it is checked. Comments have their own page (`comment-style.md`), and so do names (`naming-conventions.md`).

## 1. A module lives in the layer its imports allow

Pick the package by what the module needs to import, not by what it is about. A function that runs SQL is
under `backend/infrastructure/postgres`; a function that decides is under `backend/application`; a route only
validates, calls one application function and shapes the answer.

Good — `backend/api/routes/health.py` asks the application:

```python
is_database_reachable = health.is_database_reachable(request.app.state.pool)
```

Bad — the same route reaching into the store:

```python
from backend.infrastructure.postgres import db
```

Checked by `tests/architecture/test_import_rule.py`; this exact import is one of its synthetic cases.

Packages are regular packages on purpose, not namespace packages: every directory of modules has an
`__init__.py`, and each marker's first line states its package's role. Checked by
`tests/architecture/test_package_markers.py`.

## 2. A function does one thing a reader can name

A function that needs more than a screen is split by what its parts do. The turn loop is three pieces in
`backend/application/agent_loop.py`: `run_turn` sets the turn up, `_take_steps` loops, and `_RunningTurn` holds
the turn's state with one method per thing that can happen (`ask_model`, `run_tool_calls`, `decide`,
`fall_back`, `complete`). No function body under `backend/` or `evals/` is longer than 50 lines.

Checked by review.

## 3. A value with a meaning has a name

Numbers and strings that carry a decision are module-level constants in `UPPER_SNAKE_CASE`, placed in the
module that uses them.

Good — `backend/application/agent_loop.py`:

```python
TURN_DEADLINE_S = 60.0
MODEL_TIMEOUT_S = 30.0
MAX_STEPS = 8
```

Bad:

```python
for _ in range(8):
```

Exceptions that stay as literals: HTTP statuses in the routes (`200`, `201`, `503`) and in the `STATUS`
table of `backend/api/problems.py`, where the number is the meaning. Units go in the name: `_S` for seconds,
`_MS` for milliseconds, `_CHARS`, `_CENTS`.

Checked by review.

## 4. SQL is plain, parameterised and kept with the function that runs it

A statement is a string in the store module that executes it. A statement that carries a guarantee, or is
used from more than one function, is a named module-level constant with a one-line comment saying what it
guarantees.

Good — `backend/infrastructure/postgres/turns.py`:

```python
# only the turn holding the conversation matches, so a turn that lost it writes nothing
RELEASE_AND_WRITE = """
    WITH released AS (
        UPDATE conversations SET busy_turn = NULL, busy_until = NULL
         WHERE id = %(conversation)s AND busy_turn = %(turn)s
    ...
```

Values are always passed as parameters (`%s`, `%(name)s`). The only text formatted into a statement is a
constant of the same module, such as a column list (`COLUMNS` in `incident_fixtures.py`) or the key predicate
(`HOLDS_KEY` in `actions.py`).

A status change and its event row are written in one transaction, and every status change is a
compare-and-set (`UPDATE ... WHERE status = ... RETURNING`), so the function returns whether it won.

Checked by review; the behaviour of the statements by `tests/integration/test_refund_races.py` and `tests/integration/test_lease.py`.

## 5. Errors are raised as `AppError`, where they happen, and never translated on the way up

A module defines the error it raises, as a subclass of one category from `backend/core/errors.py`. Callers
catch an error only to handle it — hand it to the model, retry, mark an action — never to re-shape it for
HTTP.

Good — `backend/application/actions/resolve.py`:

```python
if not store.resolve(pool, action_id, outcome, actor=resolver, detail=detail):
    raise NotInReview(action)
```

Bad — a route building the response itself:

```python
except NotInReview as error:
    return JSONResponse({"detail": str(error)}, status_code=409)
```

`evals/` is outside this rule: it is not part of the service and raises built-in exceptions (`RuntimeError`,
`ValueError`).

Checked by `tests/architecture/test_error_model.py`. Details in `logging-and-errors.md`.

## 6. Diagnostics go through the one logger

`log.event(name, outcome=..., ...)` from `backend/core/logging.py` is the only way to write a diagnostic. No
`import logging`, and no `print` outside `backend/cli`, where it is a command's output.

Checked by `tests/architecture/test_logging_rule.py`.

## 7. Dependencies are passed in

A function takes what it needs as arguments: the pool, the providers, the clock's value (`now`), the timing.
Nothing reads a global or the environment except `load_settings` in `backend/core/config.py`, and only
`main.py` and the commands under `backend/cli` construct infrastructure.

Good — `backend/application/actions/execute.py`:

```python
def drive(pool, providers, action_id, timing=Timing()) -> dict[str, Any]:
```

A test passes its own `Timing`, with short waits, instead of patching the module.

Checked by review; the construction rule by `tests/architecture/test_import_rule.py` (the application cannot import the
model adapter or the provider mocks).

## 8. Types and data shapes

- Every function has type annotations on its parameters and its return value; the one exception is the
  `lifespan` generator inside `create_app`, which has no return annotation.
- Data that crosses the model boundary or the HTTP boundary is a Pydantic model with
  `ConfigDict(extra="forbid")` (`Draft`, the tool argument classes) or a request model in the route module.
- Small fixed records are frozen dataclasses (`Policy`, `Settings`, `ToolContext`, `LLMReply`).
- Rows read from the database travel as dictionaries.

Checked by review. There is no type checker or linter in `requirements.txt`.

## 9. Formatting

- Imports are in three groups — standard library, third party, this repository — each sorted.
- Lines run long where a statement reads better on one line; there is no enforced limit and no formatter.
- Double quotes for strings.

Checked by review.

## 10. Tests

- A test's name is a sentence that states the behaviour:
  `test_a_ticket_sent_again_under_another_customer_is_refused_and_nothing_is_stored`.
- A test lives in the folder named after what it touches: `tests/unit` for a pure function (no database, no
  HTTP), `tests/integration` for anything on the real database or through the HTTP client,
  `tests/architecture` for a test that reads the source, `tests/e2e` for the service as its own process.
  A file that would mix two kinds is two files of the same name, one per folder. What each folder may and
  may not use: `testing.md`.
- Integration tests go through the HTTP client and a real database (`client`, `empty_database` in
  `tests/conftest.py`); the model is scripted with `llm.script(call(...), answer(...))`.
- An assertion is never weakened to make a change pass. A changed behaviour changes the assertion on purpose
  and says so in the commit.
- Every test asserts an outcome, and the suite keeps the coverage of `backend/` and `main.py` at 95% or
  more; the test command fails below it (`testing.md`).
