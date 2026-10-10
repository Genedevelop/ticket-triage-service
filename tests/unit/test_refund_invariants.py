import pytest

from backend.application.actions import refund
from backend.application.tools.registry import read_tool_names
from backend.domain import action_status as machine
from backend.domain import rules
from backend.domain.policy import policy_for
from backend.domain.ports import ProviderError
from tests.unit.test_rules import ANSWERED

APPROVED_REFUND = {"id": "a1", "payload": {"charge_id": "ch_1", "amount_cents": 2999, "currency": "USD"}}


# a billing system with one unrefunded charge that answers every refund call with the given error
class RefusingBilling:
    def __init__(self, error: ProviderError) -> None:
        self.error = error
        self.refund_calls: list[dict] = []

    def get_charge(self, charge_id: str, timeout_s: float) -> dict:
        return {"charge_id": charge_id, "refund": None}

    def refund(self, charge_id: str, **parameters) -> dict:
        self.refund_calls.append({"charge_id": charge_id, **parameters})
        raise self.error


def test_refund_is_the_only_listed_effect_and_an_unlisted_one_needs_a_person():
    assert policy_for("refund").approver == "person"
    assert policy_for("refund").is_key_held is True
    assert policy_for("refund").parked == machine.NEEDS_REVIEW
    assert policy_for("delete_account").approver == "person"


@pytest.mark.parametrize("status", sorted(machine.STATUSES))
def test_an_unfinished_action_holds_the_ticket_in_front_of_a_person(status):
    decision = {**ANSWERED, "actions": [{"id": "a1", "kind": "refund", "status": status}]}

    final, overrides = rules.apply(decision, [{"kind": "tool", "name": "kb_search", "arguments": {}, "result": {}, "ok": True, "duration_ms": 1}], read_tools=read_tool_names())

    if status in ("succeeded", "failed", "rejected", "stale"):
        assert (final, overrides) == ("auto_respond", [])
    else:
        assert final == "escalate_to_human"
        assert overrides == [
            {"rule": "R1", "from": "auto_respond", "to": "escalate_to_human", "reason": f"refund a1 is {status}"}
        ]


def test_a_refund_the_provider_refuses_is_failed_with_the_refusal_as_its_reason_after_one_call_under_the_actions_id():
    refusing = RefusingBilling(ProviderError("not_refundable", "a charge in state disputed cannot be refunded"))

    outcome = refund.perform(APPROVED_REFUND, refusing, timeout_s=5)

    assert outcome == (machine.FAILED, None, {"reason": "not_refundable"})
    assert refusing.refund_calls == [
        {"charge_id": "ch_1", "amount_cents": 2999, "currency": "USD", "idempotency_key": "a1", "timeout_s": 5},
    ]


def test_a_refund_call_whose_outcome_is_unknown_is_passed_on_for_another_attempt():
    unanswered = ProviderError("timeout", "the provider did not answer in time", is_retryable=True)

    with pytest.raises(ProviderError) as passed_on:
        refund.perform(APPROVED_REFUND, RefusingBilling(unanswered), timeout_s=5)

    assert passed_on.value is unanswered
