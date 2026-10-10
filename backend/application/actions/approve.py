from typing import Any
from uuid import UUID

from backend.application.actions import execute
from backend.application.actions.registry import effect_for
from backend.core.errors import Conflict, Internal, Unavailable
from backend.domain import action_status
from backend.domain.ports import ProviderError, Providers
from backend.infrastructure.postgres import actions as store

APPROVE = "approve"
REJECT = "reject"


class Contradiction(Conflict):
    code = "verdict-contradiction"
    title = "Verdict contradicts the recorded one"

    def __init__(self, action: dict[str, Any]) -> None:
        super().__init__(
            f"the action is already {action['status']}; the opposite verdict cannot be recorded",
            details={"action_id": str(action["id"])},
        )


class EvidenceUnreadable(Unavailable):
    code = "evidence-unreadable"
    title = "Evidence unreadable"

    def __init__(self, action_id: UUID, message: str) -> None:
        super().__init__(message, details={"action_id": str(action_id)})


class EvidenceCheckMissing(Internal):
    code = "evidence-check-missing"
    title = "Evidence check missing"

    def __init__(self, action: dict[str, Any]) -> None:
        super().__init__(
            f"the kind {action['kind']} has no way to read its evidence again; the action stays {action['status']}",
            details={"action_id": str(action["id"]), "kind": action["kind"]},
        )


def decide(
    pool: Any,
    providers: Providers,
    action_id: UUID,
    *,
    verdict: str,
    approver: str,
    note: str | None,
    timing: execute.Timing = execute.Timing(),
) -> dict[str, Any] | None:
    while True:
        # Step 1: Read the action, reporting one that does not exist as None
        action = store.get(pool, action_id)
        if action is None:
            return None
        # Step 2: Answer a verdict on an action that already has one
        if action["status"] != action_status.PENDING:
            return _answer_repeated_verdict(pool, providers, action, verdict, timing)
        # Step 3: Record a rejection, reading the action again when another verdict won the update
        if verdict == REJECT:
            if store.decide(pool, action_id, action_status.REJECTED, actor=approver, detail={"note": note}):
                return store.get(pool, action_id)
            continue
        # Step 4: Refuse a kind that cannot read its evidence again, because a missing check proves nothing unchanged
        read_changed_fields = effect_for(action["kind"]).changed_evidence_fields
        if read_changed_fields is None:
            raise EvidenceCheckMissing(action)
        # Step 5: Read the evidence again at the provider, approving nothing when it cannot be read
        try:
            changed_fields = read_changed_fields(action, providers, timing.provider_timeout_s)
        except ProviderError as error:
            raise EvidenceUnreadable(action_id, error.message) from error
        # Step 6: Mark the action stale instead of approving it when the evidence changed since the proposal
        if changed_fields:
            if store.decide(pool, action_id, action_status.STALE, actor=approver, detail={"changed": changed_fields}):
                return store.get(pool, action_id)
            continue
        # Step 7: Record the approval with one compare-and-set and execute the action before answering
        if store.decide(pool, action_id, action_status.APPROVED, actor=approver, detail={"note": note}):
            return execute.drive(pool, providers, action_id, timing)


def _answer_repeated_verdict(
    pool: Any, providers: Providers, action: dict[str, Any], verdict: str, timing: execute.Timing
) -> dict[str, Any]:
    # Step 1: Answer any verdict on a stale action with the action as it is
    if action["status"] == action_status.STALE:
        return action
    # Step 2: Refuse the opposite of the verdict already recorded
    recorded = REJECT if action["status"] == action_status.REJECTED else APPROVE
    if verdict != recorded:
        raise Contradiction(action)
    # Step 3: Drive an approved action whose execution is unfinished, and answer the same verdict with the action
    if action["status"] in (action_status.APPROVED, action_status.EXECUTING):
        return execute.drive(pool, providers, action["id"], timing)
    return action
