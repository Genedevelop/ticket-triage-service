# 0008 — A layered layout with enforced imports

## Context

The service was first built with packages named after what they held: `app/agent`, `app/actions`,
`app/store`, `app/llm`, `app/providers`, `app/api`, with `config.py` and `logging.py` at the top of `app/`.
These are the names of that time: the package was called `app` then and is `backend` now.
One test already kept each external library inside one package. Nothing said which package could import
which, so adding a tool, an effect kind or a route had no fixed place and no check.

## Decision

`backend/` has five layers and a package of command-line entry points; the composition root is `main.py` at
the repository root, beside the package:

```
core <- domain <- infrastructure
                  application  (domain, core, and infrastructure.postgres only)
                  api          (application, domain, core)
cli/              imports everything under backend; nothing imports it
../main.py        imports every layer, never cli; nothing under backend imports it
```

`tests/architecture/test_import_rule.py` parses every module's imports and fails on a forbidden one. It also keeps each
external library with one owner (`fastapi` and `starlette` in `backend/api`, `psycopg` in
`backend/infrastructure/postgres`, `openai` in `backend/infrastructure/llm`), keeps `backend/domain` to `pydantic`
and a list of standard-library modules that do no input or output, refuses an external library no package
owns and an import by a name built at run time, keeps the infrastructure packages apart except that
`providers` may use `postgres`, keeps the standard library's network and database clients inside
`backend/infrastructure`, lets no module under `backend/` import `evals` or `tests`, and requires every module to
belong to a layer. The full rules are in `../architecture.md`.

Extension goes through registries: `backend/application/tools/registry.py`,
`backend/application/actions/registry.py` and `backend/api/routes/__init__.py`.

## Consequences

- A new tool, effect kind, provider, model vendor or route is one module and one registry entry; the
  recipes and their limits are in `../adding-a-feature.md`.
- A route cannot read the store; two thin application modules exist only to pass a call through
  (`backend/application/health.py`, `backend/application/conversations.py`).
- `rules.py` and `decision.py` are in `backend/domain`. They ask the tool registry nothing: the loop passes the
  names of the read tools and of the tools whose articles may be cited.
- The sample data and its loader are in `backend/infrastructure/providers/sample_data.py`, so `main.py`
  imports nothing from `evals`, and the rule has no unrestricted module.
- A command that must build infrastructure goes under `backend/cli` (`python -m backend.cli.reset`); a manual check
  against the real model goes beside the evaluation (`python -m evals.smoke`).

## Rejected

Leaving the rule to review; and putting the store behind a port to make `application` free of
infrastructure imports (see `0006-tests-on-real-postgresql-without-a-store-double.md`).
