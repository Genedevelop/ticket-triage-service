# 0007 — Synchronous Python with plain SQL on PostgreSQL

Source: `design.md` §0 D7 and §13.

## Context

Every guarantee is a conditional update or a unique index that must hold between concurrent writers on one
clock, and stay true with a second instance. One instance serves turns that last seconds.

## Decision

Synchronous Python: FastAPI routes declared with `def`, psycopg 3 with a connection pool
(`backend/infrastructure/postgres/db.py`), SQL written as string constants in the store modules, and one
`schema.sql` applied at start-up. PostgreSQL is the database in development, in tests and in the evaluation.

## Consequences

- A statement in the code is the statement that runs; the claim, the park and the release-and-write
  statements can be read and tested as written.
- A thread per turn is enough for one instance. A request lasts as long as the model takes.
- There is no migration history: after a schema change the served database is rebuilt with
  `python -m backend.cli.reset`.
- Nothing in the design depends on Python; it was chosen because the model SDK is there.

## Rejected

An async stack, an ORM, a migration tool, and SQLite.
