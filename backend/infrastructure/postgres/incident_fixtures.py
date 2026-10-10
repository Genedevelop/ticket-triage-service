from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.core.errors import Internal

COLUMNS = "incident_id, dedup_key, component, region, summary, status, opened_at"
# how often an open looks again when the open incident was resolved between the insert and the read
OPEN_ROUNDS = 3


def open_or_attach(
    pool: ConnectionPool, *, incident_id: str, dedup_key: str, component: str, region: str, summary: str
) -> tuple[dict[str, Any], bool]:
    for _ in range(OPEN_ROUNDS):
        with pool.connection() as conn:
            # Step 1: Open the incident, which the unique index refuses while one is open for the dedup key
            cursor = conn.cursor(row_factory=dict_row)
            opened = cursor.execute(
                f"""
                INSERT INTO mock_incidents (incident_id, dedup_key, component, region, summary, status)
                VALUES (%s, %s, %s, %s, %s, 'open') ON CONFLICT DO NOTHING
                RETURNING {COLUMNS}
                """,
                (incident_id, dedup_key, component, region, summary),
            ).fetchone()
            if opened is not None:
                return opened, False
            # Step 2: Attach to the open incident, looking again when it was resolved between the insert and the read
            existing = cursor.execute(
                f"SELECT {COLUMNS} FROM mock_incidents WHERE dedup_key = %s AND status = 'open'", (dedup_key,)
            ).fetchone()
            if existing is not None:
                return existing, True
    # Step 3: Give up after the last round, which no ordinary interleaving reaches
    raise Internal(f"the dedup key {dedup_key} was neither free nor open after {OPEN_ROUNDS} rounds")


def list_open(pool: ConnectionPool) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            f"SELECT {COLUMNS} FROM mock_incidents WHERE status = 'open' ORDER BY opened_at, incident_id"
        ).fetchall()


def resolve(pool: ConnectionPool, incident_id: str) -> None:
    with pool.connection() as conn:
        conn.execute("UPDATE mock_incidents SET status = 'resolved' WHERE incident_id = %s", (incident_id,))
