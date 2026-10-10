from typing import Any

from backend.infrastructure.postgres import db


def is_database_reachable(pool: Any) -> bool:
    return db.is_reachable(pool)
