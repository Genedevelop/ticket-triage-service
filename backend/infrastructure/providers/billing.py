from datetime import datetime
from typing import Any
from uuid import uuid4

from backend.domain.ports import ProviderError
from backend.infrastructure.postgres import billing_fixtures
from backend.infrastructure.providers.faults import ISSUED_ID_HEX_CHARS, fault_after_acting, fault_before_acting

CURRENCY = "USD"
CENTS_PER_UNIT = 100


def _charge_as_reported(row: dict[str, Any]) -> dict[str, Any]:
    # Step 1: Describe the refund on the charge, with the idempotency key that made it
    refund = None
    if row["refund_id"] is not None:
        refund = {"refund_id": row["refund_id"], "idempotency_key": row["refund_key"], "amount_cents": row["refund_amount_cents"]}
    # Step 2: Report the charge with its owner, amount, state and refund
    return {
        "charge_id": row["charge_id"],
        "customer_id": row["customer_id"],
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "state": row["state"],
        "refund": refund,
    }


def _refund_as_reported(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "refund_id": row["refund_id"],
        "charge_id": row["charge_id"],
        "amount_cents": row["refund_amount_cents"],
        "currency": row["currency"],
        "idempotency_key": row["refund_key"],
    }


def _refund_made_with_key(made: dict[str, Any], charge_id: str, amount_cents: int, currency: str) -> dict[str, Any]:
    if (charge_id, amount_cents, currency) != (made["charge_id"], made["refund_amount_cents"], made["currency"]):
        raise ProviderError("idempotency_key_reuse", "this idempotency key was used with different parameters")
    return _refund_as_reported(made)


class MockBilling:
    def __init__(self, pool: Any) -> None:
        # Step 1: Keep the database the accounts and charges live in
        self._pool = pool
        # Step 2: Start with no fault queued; a test queues one per coming refund call
        self.faults: list[str] = []

    def add_account(self, customer_id: str, *, plan: str, last_upgrade_attempt: dict[str, Any] | None = None) -> None:
        billing_fixtures.add_account(self._pool, customer_id, {"plan": plan, "last_upgrade_attempt": last_upgrade_attempt})

    def add_charge(self, charge_id: str, customer_id: str, *, amount_cents: int, state: str, created_at: datetime) -> None:
        billing_fixtures.add_charge(
            self._pool,
            charge_id=charge_id,
            customer_id=customer_id,
            amount_cents=amount_cents,
            currency=CURRENCY,
            state=state,
            created_at=created_at,
        )

    def lookup(self, customer_id: str, now: datetime) -> dict[str, Any]:
        # Step 1: Read the account, refusing a customer the billing system does not know
        account = billing_fixtures.get_account(self._pool, customer_id)
        if account is None:
            raise ProviderError("account_not_found", "the billing system has no account for this customer")
        # Step 2: Report the plan, the last upgrade attempt and every payment attempt, oldest first
        return {
            "source": "billing system",
            "observed_at": now.isoformat(),
            "plan": account["plan"],
            "last_upgrade_attempt": account["last_upgrade_attempt"],
            "payment_attempts": [
                {
                    "charge_id": charge["charge_id"],
                    "amount": f"{charge['amount_cents'] / CENTS_PER_UNIT:.2f}",
                    "currency": charge["currency"],
                    "state": charge["state"],
                    "at": charge["created_at"].isoformat(),
                }
                for charge in billing_fixtures.list_charges(self._pool, customer_id)
            ],
        }

    def get_charge(self, charge_id: str, timeout_s: float) -> dict[str, Any]:
        # Step 1: Read the charge, refusing one the billing system does not know
        row = billing_fixtures.get_charge(self._pool, charge_id)
        if row is None:
            raise ProviderError("charge_not_found", "the billing system has no such charge")
        # Step 2: Report it with its owner, amount, state and refund
        return _charge_as_reported(row)

    def refund(self, charge_id: str, *, amount_cents: int, currency: str, idempotency_key: str, timeout_s: float) -> dict[str, Any]:
        # Step 1: Apply a queued fault that stops the call before the provider acts
        fault = fault_before_acting(self.faults, timeout_s)
        # Step 2: Refund the charge under the idempotency-key contract
        refund = self._refund_once_per_key(charge_id, amount_cents, currency, idempotency_key)
        # Step 3: Apply a queued fault that loses the answer or reports another charge or amount than the one refunded
        fault_after_acting(fault)
        if fault == "wrong_charge":
            return {**refund, "charge_id": f"{charge_id}_other"}
        if fault == "wrong_amount":
            return {**refund, "amount_cents": refund["amount_cents"] + 1}
        return refund

    def _refund_once_per_key(self, charge_id: str, amount_cents: int, currency: str, idempotency_key: str) -> dict[str, Any]:
        # Step 1: Answer a key that already made a refund with that same refund
        made = billing_fixtures.get_charge_refunded_with(self._pool, idempotency_key)
        if made is not None:
            return _refund_made_with_key(made, charge_id, amount_cents, currency)
        # Step 2: Refuse a charge that does not exist, is refunded already, is not captured, or is asked for in part
        row = billing_fixtures.get_charge(self._pool, charge_id)
        if row is None:
            raise ProviderError("charge_not_found", "the billing system has no such charge")
        if row["state"] == "refunded":
            raise ProviderError("already_refunded", "the charge has been refunded already")
        if row["state"] != "captured":
            raise ProviderError("not_refundable", f"a charge in state {row['state']} cannot be refunded")
        if (amount_cents, currency) != (row["amount_cents"], row["currency"]):
            raise ProviderError("amount_mismatch", "only the full amount of the charge can be refunded")
        # Step 3: Refund the charge only if it is still captured at this moment
        refund_id = f"re_{uuid4().hex[:ISSUED_ID_HEX_CHARS]}"
        refunded = billing_fixtures.refund_charge(self._pool, charge_id=charge_id, refund_id=refund_id, idempotency_key=idempotency_key)
        if refunded is not None:
            return _refund_as_reported(refunded)
        # Step 4: When another call refunded the charge in between, answer only the call that used the same key
        row = billing_fixtures.get_charge(self._pool, charge_id)
        if row is not None and row["refund_key"] == idempotency_key:
            return _refund_made_with_key(row, charge_id, amount_cents, currency)
        raise ProviderError("already_refunded", "the charge has been refunded already")
