# CLAUDE.md

Instructions for an AI coding agent working in this repository. This file points at the documents under
`docs/`; read the one a rule names before acting on it.

## What this is

A support-ticket triage service: a model decides who should handle a ticket and how soon, behind an HTTP
service that stores everything in PostgreSQL and records every step.
The model only proposes effects (a refund, an incident); a policy table says who approves and code executes.
Python 3.13, FastAPI, psycopg 3, plain SQL; everything runs in Docker.

## The one command

```sh
docker compose run --rm app pytest -q
```

No API key is needed: tests use their own PostgreSQL database and a scripted model. The repository is mounted
into the container, so a code change needs no rebuild; only a change to `requirements.txt` does. The whole
suite must pass before every commit.

The command also measures coverage of `backend/` and `main.py` (lines and branches, `.coveragerc`) and fails
below the target. **Coverage must stay at or above 95%**: new code arrives with tests that assert its
behaviour, never with `# pragma: no cover`, an `omit` rule, a lowered `fail_under`, or a test that only runs
lines. A line that cannot be reached is reported to the owner, not excluded. If the image predates the
coverage tool and cannot be rebuilt, run
`docker compose run --rm app sh -c "pip install -q --user coverage==7.16.2 pytest-cov==7.1.0 && python -m pytest -q"`.

A test goes in the folder named after what it touches: `tests/unit` (pure functions, no database, no HTTP),
`tests/integration` (real PostgreSQL and/or the in-process HTTP client), `tests/architecture` (tests that
read the source), `tests/e2e` (the server started as a separate process and reached over a socket; few, each
costs a second). Full text: `docs/testing.md`. One folder runs alone by naming it with the measurement off:
`docker compose run --rm app pytest -q --no-cov tests/unit`. Never run two test commands at once: they
share one test database.

## Layer rules

Full text: `docs/architecture.md`. Enforced by `tests/architecture/test_import_rule.py`.

| Package | May import |
|---|---|
| `backend/core` | nothing outside itself |
| `backend/domain` | itself, `backend.core`; `pydantic` and a fixed list of standard-library modules only |
| `backend/infrastructure/postgres`, `backend/infrastructure/llm` | itself, `backend.domain`, `backend.core` |
| `backend/infrastructure/providers` | itself, `backend.infrastructure.postgres`, `backend.domain`, `backend.core` |
| `backend/application` | `backend.domain`, `backend.core`, `backend.infrastructure.postgres` — never `infrastructure.llm` or `infrastructure.providers` |
| `backend/api` | `backend.application`, `backend.domain`, `backend.core` — never `backend.infrastructure` |
| `main.py` (repository root) | every package under `backend` except `backend.cli`, never `evals` or `tests`; constructs infrastructure; nothing under `backend` imports it |
| `backend/cli` | everything under `backend`; command-line entry points (`python -m backend.cli.reset`); nothing under `backend` imports it |

`fastapi` and `starlette` only under `backend/api`; `psycopg` only under `backend/infrastructure/postgres`; `openai`
only under `backend/infrastructure/llm`; any other external library but `pydantic` fails the test.
Standard-library network and database clients (`urllib.request`, `http.client`, `socket`, `sqlite3`, ...)
only under `backend/infrastructure`. No module under `backend/` imports `main`, `evals` or `tests`. The library
rules hold for `main.py` too.

## Comments and names

Full text: `docs/comment-style.md`, `docs/naming-conventions.md`, `docs/code-style.md`.

- In `backend/`, `main.py` and `evals/`, a function with two or more steps gets `# Step N: <one English sentence>` above
  each step, numbered from 1. A single-step function gets no comment.
- No docstrings anywhere. If a name needs explaining, rename it.
- At module level, one plain lower-case `#` line above a constant, a SQL statement, a field or a table row
  whose reason is not in its name.
- Every `__init__.py` opens with one plain lower-case `#` line saying what the package is and, for a layer
  under `backend/`, what it may import (`tests/architecture/test_package_markers.py`). No `__all__`, no
  re-exports.
- Tests: no step comments; the test name is a sentence stating the behaviour.
- Root and configuration files: plain comments under `# --- Category ---` headers.
- `snake_case` for modules, functions and variables; `PascalCase` for classes and exceptions;
  `UPPER_SNAKE_CASE` for constants; booleans read as predicates (`is_`, `has_`, `can_`); files under `docs/`
  are `kebab-case.md`; error codes are `kebab-case`.
- Never put task ids, ticket keys or design section numbers in code or comments.

## Errors and logs

Full text: `docs/logging-and-errors.md`.

- Raise a subclass of one category from `backend/core/errors.py`, defined in the module that raises it — or one
  of the shared errors already in that file (`NotFound`, `Busy`, `Lost`, `OtherCustomer`, `Internal`); never an
  exception class of the language. Routes never catch; `backend/api/problems.py` maps the category to a status
  once.
- Log only with `log.event(...)` from `backend/core/logging.py`. No `import logging` outside that module,
  and no `print` outside it and `backend/cli`, where it is the command's output. Anything that may
  hold customer text goes in `payload`.

## Adding a feature

Follow the recipe in `docs/adding-a-feature.md`: a read tool, an effect kind, a provider, a model vendor, a
route, an intake channel, a decision field, a prompt version. Each names the files to touch, the tests to
add, and what it does not cover. If a change fits no recipe, say so before starting.

## Hard don'ts

- Never read, print, copy or name in a command the environment file or any key. No task here needs the API
  key; the key names are in the Settings table of `README.md`.
- Never weaken or delete a test assertion to make a change pass. A behaviour that changes on purpose changes
  its assertion on purpose, and the commit says so.
- No new dependency without asking; `requirements.txt` is pinned.
- Never `git push`, never touch `main`, never rewrite history.
- Do not run the evaluation (`python -m evals.run`): it calls the real model and is run by whoever holds
  the key.
- Do not change the text of a prompt file in place, including its `<!-- Why: ... -->` comments; a change
  in meaning is a new file (`docs/adding-a-feature.md`).
- Do not add a repository interface or a fake store: the store is deliberately not behind a port
  (`docs/decisions/0006-tests-on-real-postgresql-without-a-store-double.md`).
- Do not change the meaning of a SQL statement without a test that proves the new one on real PostgreSQL
  (`tests/integration/test_refund_races.py` races two connections).
- Commits are small Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `style:`, `build:`) with
  files staged by explicit path and no trailers.
