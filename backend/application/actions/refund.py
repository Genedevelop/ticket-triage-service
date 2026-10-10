from datetime import datetime
from typing import Any
from uuid import UUID

from backend.core.errors import Denied
from backend.domain import action_status, effect_keys
from backend.domain.policy import policy_for
from backend.domain.ports import Billing, ProviderError
from backend.infrastructure.postgres import actions as store

KIND = "refund"
# what a refund's answer must repeat from the action's payload to count as the refund that was asked for
PAYLOAD_FIELDS = ("charge_id", "amount_cents", "currency")


class RefundDenied(Denied):
    code = "refund-denied"
    title = "Refund denied"

    def __init__(self, reason: str, message: str, action: dict[str, Any] | None = None) -> None:
        super().__init__(message, details={"reason": reason})
        self.reason = reason
        self.action = action


def propose(
    pool: Any,
    billing: Billing,
    *,
    conversation_id: UUID,
    turn_id: UUID,
    customer_id: str,
    charge_id: str,
    reason: str,
    now: datetime,
    timeout_s: float,
) -> tuple[dict[str, Any], bool]:
    # Step 1: Read the charge at the provider
    try:
        charge = billing.get_charge(charge_id, timeout_s)
    except ProviderError as error:
        if error.error_type != "charge_not_found":
            raise
        charge = None
    # Step 2: Deny another customer's charge exactly like one that does not exist
    if charge is None or charge["customer_id"] != customer_id:
        raise RefundDenied("charge_not_found", "this customer has no such charge")
    # Step 3: Deny a charge that is not a captured payment
    if charge["state"] == "authorized":
        raise RefundDenied("not_refundable", "the charge is a hold, not a captured payment: it needs a void, which this service does not do")
    if charge["state"] != "captured":
        raise RefundDenied(f"charge_{charge['state']}", f"the charge is {charge['state']}, so there is nothing to refund")
    # Step 4: Propose the refund with the amount and currency of the charge, never the model's
    policy = policy_for(KIND)
    action, is_created = store.propose(
        pool,
        kind=KIND,
        key=effect_keys.refund_key(charge_id),
        is_key_held=policy.is_key_held,
        status=action_status.PENDING,
        payload={"charge_id": charge_id, "amount_cents": charge["amount_cents"], "currency": charge["currency"]},
        evidence={**charge, "observed_at": now.isoformat()},
        actor="model",
        conversation_id=conversation_id,
        detail={"reason": reason, "turn_id": str(turn_id)},
    )
    # Step 5: Deny the proposal when a person already rejected the refund of this charge
    if action["status"] == action_status.REJECTED:
        rejected = next(event for event in store.get(pool, action["id"])["events"] if event["type"] == "rejected")
        note = (rejected["detail"] or {}).get("note") or "no note"
        raise RefundDenied("rejected_by_a_person", f"this refund was rejected by {rejected['actor']}: {note}", action)
    return action, is_created


def changed_evidence_fields(action: dict[str, Any], billing: Billing, timeout_s: float) -> list[str]:
    # Step 1: Read the charge again, counting one that no longer exists as changed
    try:
        charge = billing.get_charge(action["payload"]["charge_id"], timeout_s)
    except ProviderError as error:
        if error.error_type != "charge_not_found":
            raise
        return ["charge"]
    # Step 2: Name the fields that differ from the snapshot taken at the proposal
    return [name for name, value in charge.items() if action["evidence"].get(name) != value]


def perform(action: dict[str, Any], billing: Billing, timeout_s: float) -> tuple[str, str | None, dict[str, Any]]:
    # Step 1: Look for a refund already on the charge: one made with this action's id for its amount is ours
    payload = action["payload"]
    own_key = str(action["id"])
    try:
        made = billing.get_charge(payload["charge_id"], timeout_s)["refund"]
        if made is not None:
            if made["idempotency_key"] == own_key and made["amount_cents"] == payload["amount_cents"]:
                return action_status.SUCCEEDED, made["refund_id"], {"found_on_charge": True}
            return action_status.FAILED, None, {"reason": "already_refunded", "refund": made}
        # Step 2: Ask the provider for the refund, with the action's id as the idempotency key
        refund = billing.refund(
            payload["charge_id"],
            amount_cents=payload["amount_cents"],
            currency=payload["currency"],
            idempotency_key=own_key,
            timeout_s=timeout_s,
        )
    except ProviderError as error:
        # Step 3: Pass on a failure that leaves the outcome unknown, and fail the action on a refusal
        if error.is_retryable:
            raise
        return action_status.FAILED, None, {"reason": error.error_type}
    # Step 4: Send a refund that answers for another charge, amount or currency to a person
    answered = {name: refund.get(name) for name in PAYLOAD_FIELDS}
    if answered != payload:
        return action_status.NEEDS_REVIEW, refund.get("refund_id"), {"reason": "answer_mismatch", "answered": answered}
    return action_status.SUCCEEDED, refund["refund_id"], {}
