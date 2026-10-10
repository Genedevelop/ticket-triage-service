from psycopg_pool import ConnectionPool

from backend.infrastructure.postgres import db


def reset(pool: ConnectionPool) -> None:
    # Step 1: Drop every table with its rows
    with pool.connection() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    # Step 2: Create the tables again from the schema file
    db.apply_schema(pool)
