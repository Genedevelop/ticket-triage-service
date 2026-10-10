from pathlib import Path

from psycopg_pool import ConnectionPool

SCHEMA_FILE = Path(__file__).with_name("schema.sql")
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 10
POOL_OPEN_TIMEOUT_S = 30
PING_TIMEOUT_S = 3


def open_pool(database_url: str) -> ConnectionPool:
    # Step 1: Configure connections that commit each statement unless a transaction is started explicitly
    pool = ConnectionPool(
        database_url, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE, kwargs={"autocommit": True}, open=False
    )
    # Step 2: Open the pool and wait until the database accepts a connection
    pool.open(wait=True, timeout=POOL_OPEN_TIMEOUT_S)
    return pool


def apply_schema(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute(SCHEMA_FILE.read_text())


def is_reachable(pool: ConnectionPool) -> bool:
    try:
        with pool.connection(timeout=PING_TIMEOUT_S) as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)
    except Exception:
        return False
