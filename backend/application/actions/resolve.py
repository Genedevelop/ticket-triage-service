from typing import Any
from uuid import UUID

from backend.core.errors import Conflict
from backend.infrastructure.postgres import actions as store


class NotInReview(Conflict):
    code = "action-not-in-review"
    title = "Action is not waiting for a resolution"

    def __init__(self, action: dict[str, Any]) -> None:
        super().__init__(
            f"the action is {action['status']}; only an action in needs_review can be resolved",
            details={"action_id": str(action["id"]), "action_status": action["status"]},
        )


def record(pool: Any, action_id: UUID, *, outcome: str, resolver: str, note: str | None) -> dict[str, Any] | None:
    # Step 1: Read the action, reporting one that does not exist as None
    action = store.get(pool, action_id)
    if action is None:
        return None
    # Step 2: Record the outcome the person found, refusing an action that is not waiting for one
    detail = {"outcome": outcome, "note": note}
    if not store.resolve(pool, action_id, outcome, actor=resolver, detail=detail):
        raise NotInReview(action)
    return store.get(pool, action_id)
