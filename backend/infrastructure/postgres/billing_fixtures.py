from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

ACCOUNT = "billing_account"
CHARGE_COLUMNS = "charge_id, customer_id, amount_cents, currency, state, refund_id, refund_key, refund_amount_cents"


def add_account(pool: ConnectionPool, customer_id: str, account: dict[str, Any]) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO mock_fixtures (kind, key, body) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (ACCOUNT, customer_id, Jsonb(account)),
        )


def add_charge(
    pool: ConnectionPool, *, charge_id: str, customer_id: str, amount_cents: int, currency: str, state: str, created_at: datetime
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO mock_charges (charge_id, customer_id, amount_cents, currency, state, created_at)
            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (charge_id, customer_id, amount_cents, currency, state, created_at),
        )


def get_account(pool: ConnectionPool, customer_id: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT body FROM mock_fixtures WHERE kind = %s AND key = %s", (ACCOUNT, customer_id)).fetchone()
    return row[0] if row else None


def list_charges(pool: ConnectionPool, customer_id: str) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            """
            SELECT charge_id, amount_cents, currency, state, created_at FROM mock_charges
             WHERE customer_id = %s ORDER BY created_at, charge_id
            """,
            (customer_id,),
        ).fetchall()


def get_charge(pool: ConnectionPool, charge_id: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            f"SELECT {CHARGE_COLUMNS} FROM mock_charges WHERE charge_id = %s", (charge_id,)
        ).fetchone()


def get_charge_refunded_with(pool: ConnectionPool, idempotency_key: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            f"SELECT {CHARGE_COLUMNS} FROM mock_charges WHERE refund_key = %s", (idempotency_key,)
        ).fetchone()


def refund_charge(pool: ConnectionPool, *, charge_id: str, refund_id: str, idempotency_key: str) -> dict[str, Any] | None:
    with pool.connection() as conn:
        return conn.cursor(row_factory=dict_row).execute(
            f"""
            UPDATE mock_charges SET state = 'refunded', refund_id = %s, refund_key = %s, refund_amount_cents = amount_cents
             WHERE charge_id = %s AND state = 'captured'
            RETURNING {CHARGE_COLUMNS}
            """,
            (refund_id, idempotency_key, charge_id),
        ).fetchone()
