import sys

from backend.core.config import load_settings
from backend.infrastructure.postgres import db
from backend.infrastructure.postgres.reset import reset


def main() -> int:
    # Step 1: Rebuild the database the service is configured with
    pool = db.open_pool(load_settings().database_url)
    try:
        reset(pool)
    finally:
        pool.close()
    # Step 2: Say what was done and what the operator does next
    print("the database is empty and has the current schema; restart the service to load the seed data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
