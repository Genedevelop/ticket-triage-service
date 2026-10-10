# 0006 — Tests run on real PostgreSQL; the store has no double and no port

Source: `design.md` §0 D6 and §10.

## Context

Every guarantee in this service is a SQL statement: a compare-and-set on a status column, a partial unique
index, a lease on the database clock. `design.md` records that in the first round of this project a fake
repository diverged from PostgreSQL and hid an ordering defect on the money path.

## Decision

There is no in-memory double of the store. `tests/conftest.py` creates a database of its own on the real
server at the start of every run, and every integration and end-to-end test goes through it. The model is the only thing replaced
(`backend/infrastructure/llm/scripted.py`), and the providers are mocks that keep their state in the same
database.

Because nothing would ever stand in for it, the store is **not** behind a port: `backend/application` imports
`backend.infrastructure.postgres` directly. This is the one place the application depends on infrastructure, and
`tests/architecture/test_import_rule.py` allows exactly that package and no other.

## Consequences

- The races are tested on two real connections (`tests/integration/test_refund_races.py`), and a test of a lock tests
  the lock that runs in production.
- The suite needs Docker and a PostgreSQL container; it cannot run without one.
- Application functions take a `pool` and call store functions, so the store's function signatures are the
  seam. Replacing PostgreSQL would mean rewriting `backend/infrastructure/postgres/` and re-proving every
  guarantee, not swapping an adapter.
- The model and the providers do go through ports (`backend/domain/ports.py`), because they are replaced in
  tests and in the evaluation.

## Rejected

A fake repository, and a repository port with a single implementation.
