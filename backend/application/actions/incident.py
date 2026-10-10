from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from backend.core.errors import Denied
from backend.domain import action_status, effect_keys
from backend.domain.policy import policy_for
from backend.domain.ports import Incidents, ProviderError
from backend.infrastructure.postgres import actions as store

KIND = "incident"


class IncidentDenied(Denied):
    code = "incident-denied"
    title = "Incident denied"

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, details={"reason": reason})
        self.reason = reason


def open(
    pool: Any,
    drive: Callable[[UUID], dict[str, Any]],
    *,
    conversation_id: UUID,
    turn_id: UUID,
    component: str,
    region: str,
    summary: str,
    now: datetime,
) -> tuple[dict[str, Any], bool]:
    # Step 1: Deny a request beyond the number of incidents the policy allows one turn
    policy = policy_for(KIND)
    if policy.per_turn is not None and store.proposed_in_turn(pool, KIND, conversation_id, turn_id) >= policy.per_turn:
        raise IncidentDenied("one_incident_per_turn", "an incident was already requested in this turn; at most one is allowed per turn")
    # Step 2: Propose the incident already approved, because its policy row is the approver
    action, is_created = store.propose(
        pool,
        kind=KIND,
        key=effect_keys.incident_key(conversation_id, component, region),
        is_key_held=policy.is_key_held,
        status=action_status.APPROVED,
        payload={"component": component, "region": region, "summary": summary},
        evidence={"observed_at": now.isoformat()},
        actor="model",
        conversation_id=conversation_id,
        detail={"turn_id": str(turn_id)},
        approver=policy.approver,
    )
    # Step 3: Leave an action that already holds the key to whoever runs it
    if not is_created:
        return action, False
    # Step 4: Execute the new action at once
    return drive(action["id"]), True


def perform(action: dict[str, Any], incidents: Incidents, timeout_s: float) -> tuple[str, str | None, dict[str, Any]]:
    # Step 1: Ask the incident system to open the incident, or to attach to the one open for the same dedup key
    payload = action["payload"]
    try:
        answered = incidents.open(
            effect_keys.incident_dedup_key(payload["component"], payload["region"]),
            component=payload["component"],
            region=payload["region"],
            summary=payload["summary"],
            timeout_s=timeout_s,
        )
    except ProviderError as error:
        # Step 2: Pass on a failure that leaves the outcome unknown, and fail the action on a refusal
        if error.is_retryable:
            raise
        return action_status.FAILED, None, {"reason": error.error_type}
    # Step 3: Report the incident and whether it was open already
    return action_status.SUCCEEDED, answered["incident_id"], {"already_open": answered["already_open"]}

