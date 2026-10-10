import pytest

from backend.application.actions import incident
from backend.application.actions.execute import Timing
from backend.application.tools.registry import read_tool_names
from backend.domain import action_status as machine
from backend.domain import rules
from backend.domain.policy import policy_for
from backend.domain.ports import ProviderError
from tests.unit.test_rules import DRAFT

ROUTED = {**DRAFT, "next_action": "route_to_specialist", "operator_reply": "Noted."}
APPROVED_INCIDENT = {"id": "a1", "payload": {"component": "login", "region": "thailand", "summary": "Error 500 at login"}}


# an incident system that answers every open call with the given error
class RefusingIncidents:
    def __init__(self, error: ProviderError) -> None:
        self.error = error
        self.calls: list[str] = []

    def open(self, dedup_key: str, **parameters):
        self.calls.append(dedup_key)
        raise self.error


def test_an_incident_is_approved_by_its_policy_row_once_per_turn_and_does_not_keep_its_key():
    policy = policy_for("incident")

    assert policy.approver == "policy:incident"
    assert policy.is_key_held is False
    assert policy.parked == machine.FAILED
    assert policy.per_turn == 1
    assert policy.max_attempts == 3
    assert policy_for("refund").per_turn is None
    assert Timing().waits_s == (1.0, 3.0)


def test_the_incident_rule_reads_the_first_attempt_of_each_incident_created_since_the_last_decision():
    decision = {**ROUTED, "actions": []}

    def created(*first_attempt):
        events = [{"type": "proposed", "detail": {}}, {"type": "claimed", "detail": {"attempt": 1}}]
        events += [{"type": "provider_result", "detail": detail} for detail in first_attempt]
        return {"id": "a1", "kind": "incident", "status": "succeeded", "events": events}

    opened = created({"attempt": 1, "outcome": "succeeded", "already_open": False})
    joined = created({"attempt": 1, "outcome": "succeeded", "already_open": True})
    lost = created({"attempt": 1, "outcome": "unknown"}, {"attempt": 2, "outcome": "succeeded", "already_open": True})
    unfinished = created()

    assert rules.apply(decision, [], [joined], read_tools=read_tool_names()) == ("route_to_specialist", [])
    assert rules.apply(decision, [], [], read_tools=read_tool_names()) == ("route_to_specialist", [])
    for incident_row in (opened, lost, unfinished):
        final, overrides = rules.apply(decision, [], [joined, incident_row], read_tools=read_tool_names())
        assert final == "escalate_to_human"
        assert [override["rule"] for override in overrides] == ["R2"]


def test_an_incident_the_incident_system_refuses_is_failed_with_the_refusal_as_its_reason():
    refusing = RefusingIncidents(ProviderError("component_unknown", "no such component", is_retryable=False))

    outcome = incident.perform(APPROVED_INCIDENT, refusing, timeout_s=1)

    assert outcome == (machine.FAILED, None, {"reason": "component_unknown"})
    assert len(refusing.calls) == 1


def test_an_incident_call_whose_outcome_is_unknown_is_passed_on_for_another_attempt():
    unanswered = ProviderError("timeout", "the incident system did not answer", is_retryable=True)

    with pytest.raises(ProviderError) as passed_on:
        incident.perform(APPROVED_INCIDENT, RefusingIncidents(unanswered), timeout_s=1)

    assert passed_on.value is unanswered
