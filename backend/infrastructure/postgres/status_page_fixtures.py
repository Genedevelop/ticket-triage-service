from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

STATUS_PAGE = "status_page"
PUBLIC = "public"


def add_status_page(pool: ConnectionPool, page: dict[str, Any]) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO mock_fixtures (kind, key, body) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (STATUS_PAGE, PUBLIC, Jsonb(page)),
        )


def get_status_page(pool: ConnectionPool) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT body FROM mock_fixtures WHERE kind = %s AND key = %s", (STATUS_PAGE, PUBLIC)).fetchone()
    return row[0] if row else None
