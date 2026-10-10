import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from backend.application.actions.registry import effect_for
from backend.domain.policy import kinds_approved_by_a_person, policy_for
from backend.domain.ports import ProviderError, Providers
from backend.infrastructure.postgres import actions as store


@dataclass(frozen=True)
class Timing:
    # seconds to wait after an attempt whose outcome is unknown, before the next one
    waits_s: tuple[float, ...] = (1.0, 3.0)
    provider_timeout_s: float = 10.0
    # seconds a claim keeps other executors away
    lease_s: float = 60.0


def drive(pool: Any, providers: Providers, action_id: UUID, timing: Timing = Timing()) -> dict[str, Any]:
    # Step 1: Read the action's policy row and effect, refusing a kind with no effect before any attempt is made
    action = store.get(pool, action_id)
    policy = policy_for(action["kind"])
    effect = effect_for(action["kind"])
    while True:
        # Step 2: Claim one attempt, parking an action whose attempts are spent and whose lease is free
        claimed = store.claim(pool, action_id, max_attempts=policy.max_attempts, lease_s=timing.lease_s)
        if claimed is None:
            store.park(pool, action_id, policy.parked, max_attempts=policy.max_attempts, lease_s=timing.lease_s)
            return store.get(pool, action_id)
        # Step 3: Call the provider outside any transaction
        attempt = claimed["attempts"]
        try:
            status, provider_ref, detail = effect.perform(claimed, providers, timing.provider_timeout_s)
        except ProviderError as error:
            # Step 4: Release an attempt whose outcome is unknown, wait and go round again
            store.release(pool, action_id, attempt, detail={"error": error.error_type})
            if attempt <= len(timing.waits_s):
                time.sleep(timing.waits_s[attempt - 1])
            continue
        # Step 5: Record the outcome the provider reported and answer with the action as it is stored
        store.record_outcome(pool, action_id, attempt, status, provider_ref=provider_ref, detail=detail)
        return store.get(pool, action_id)


def recover_unfinished(pool: Any, providers: Providers, timing: Timing = Timing()) -> None:
    for kind in kinds_approved_by_a_person():
        # Step 1: Free the leases a dead process left on the unfinished actions of a kind a person approves
        action_ids = store.free_unfinished(pool, kind)
        # Step 2: Drive each of them to its end, oldest first
        for action_id in action_ids:
            drive(pool, providers, action_id, timing)
