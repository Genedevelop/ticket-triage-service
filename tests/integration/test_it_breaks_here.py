# each row of the design's "It breaks here" table as a named sequence, driven by the fault modes of the mocks
import os
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.application.actions.execute import Timing
from backend.core.config import load_settings
from backend.infrastructure.llm.scripted import ScriptedLLM, answer
from backend.infrastructure.postgres import actions as action_store
from backend.infrastructure.providers.faults import SimulatedCrash
from evals.sample import request_body
from main import create_app
from tests.conftest import NOW, execute
from tests.integration.test_incident import holders, incident, rule_names, tool_steps
from tests.integration.test_refund_invariants import DUPLICATE, post_ticket, propose, verdict
from tests.unit.test_rules import DRAFT

# the design's waits and time-out, shortened so the suite does not sit through them
SHORT = Timing(waits_s=(0.05, 0.1), provider_timeout_s=0.02)
LEASE_HELD = "SELECT claimed_at > now() - interval '60 seconds' AS held FROM actions"
LEASE_RAN_OUT = "UPDATE actions SET claimed_at = now() - interval '61 seconds'"


# the executor waits and times out in hundredths of a second
@pytest.fixture(autouse=True)
def short_waits(client):
    client.app.state.action_timing = SHORT


# every call that reaches a provider's acting method, in order: the method and the key it was given
@pytest.fixture()
def calls(providers, monkeypatch):
    made = []

    def counting(method, real):
        def counted(target, **parameters):
            made.append((method, parameters.get("idempotency_key", target)))
            return real(target, **parameters)

        return counted

    monkeypatch.setattr(providers.billing, "refund", counting("refund", providers.billing.refund), raising=False)
    monkeypatch.setattr(providers.incidents, "open", counting("open", providers.incidents.open), raising=False)
    return made


# A second application on the same database, as the service is after the process died and came back
def restart(database_url, providers):
    settings = load_settings({**os.environ, "DATABASE_URL": database_url, "OPENAI_API_KEY": ""})
    app = create_app(llm=ScriptedLLM(), providers=providers, clock=lambda: NOW, settings=settings)
    app.state.action_timing = SHORT
    return TestClient(app)


def crash(*arguments, **parameters):
    raise SimulatedCrash("the process died here")


# A refund waiting for a person; returns its conversation and its id
def proposed_refund(client, llm):
    posted = post_ticket(client, llm, propose(DUPLICATE))
    (action,) = posted["decision"]["actions"]
    return posted["conversation_id"], action["id"]


def the_action(client, conversation_id):
    (action,) = client.get(f"/conversations/{conversation_id}").json()["actions"]
    return action


def left_behind(database_url):
    (row,) = execute(database_url, "SELECT status, attempts, claimed_at FROM actions")
    return row


def resolve(client, action_id, outcome, resolver="dana", note=None):
    return client.post(f"/actions/{action_id}/resolution", json={"outcome": outcome, "resolver": resolver, "note": note})


def test_a_refund_that_breaks_before_the_claim_commits_is_left_approved_and_recovery_claims_it(
    client, llm, providers, empty_database, calls, monkeypatch
):
    conversation_id, action_id = proposed_refund(client, llm)
    with monkeypatch.context() as patch:
        patch.setattr(action_store, "claim", crash)
        with pytest.raises(SimulatedCrash):
            verdict(client, action_id, "approve")
    left = left_behind(empty_database)

    with restart(empty_database, providers) as second:
        action = the_action(second, conversation_id)

    assert (left["status"], left["attempts"]) == ("approved", 0)
    assert (action["status"], action["attempts"]) == ("succeeded", 1)
    assert calls == [("refund", action_id)]
    assert providers.billing.get_charge(DUPLICATE, 1)["refund"]["idempotency_key"] == action_id


def test_a_refund_that_breaks_after_the_claim_and_before_the_call_is_left_executing_and_recovery_calls_once(
    client, llm, providers, empty_database, calls
):
    conversation_id, action_id = proposed_refund(client, llm)
    providers.billing.faults.append("crash_before")
    with pytest.raises(SimulatedCrash):
        verdict(client, action_id, "approve")
    left = left_behind(empty_database)
    charge_left = providers.billing.get_charge(DUPLICATE, 1)
    (lease,) = execute(empty_database, LEASE_HELD)
    calls.clear()

    with restart(empty_database, providers) as second:
        action = the_action(second, conversation_id)

    assert (left["status"], left["attempts"]) == ("executing", 1)
    assert (charge_left["state"], charge_left["refund"]) == ("captured", None)
    # recovery at start-up did not wait for the lease the dead attempt still held
    assert lease["held"] is True
    assert calls == [("refund", action_id)]
    assert (action["status"], action["attempts"]) == ("succeeded", 2)
    assert providers.billing.get_charge(DUPLICATE, 1)["state"] == "refunded"


def test_an_approval_posted_again_recovers_the_refund_once_its_lease_is_free(client, llm, providers, empty_database, calls):
    _, action_id = proposed_refund(client, llm)
    providers.billing.faults.append("crash_before")
    with pytest.raises(SimulatedCrash):
        verdict(client, action_id, "approve")
    calls.clear()

    while_held = verdict(client, action_id, "approve")
    calls_while_held = list(calls)
    execute(empty_database, LEASE_RAN_OUT)
    after = verdict(client, action_id, "approve")

    assert (while_held.status_code, while_held.json()["status"]) == (200, "executing")
    assert calls_while_held == []
    assert (after.status_code, after.json()["status"], after.json()["attempts"]) == (200, "succeeded", 2)
    assert calls == [("refund", action_id)]


def test_a_refund_made_at_the_provider_and_not_recorded_is_found_on_the_charge_and_recorded_without_a_call(
    client, llm, providers, empty_database, calls
):
    conversation_id, action_id = proposed_refund(client, llm)
    providers.billing.faults.append("crash_after")
    with pytest.raises(SimulatedCrash):
        verdict(client, action_id, "approve")
    left = left_behind(empty_database)
    made = providers.billing.get_charge(DUPLICATE, 1)["refund"]
    calls.clear()

    with restart(empty_database, providers) as second:
        action = the_action(second, conversation_id)

    assert (left["status"], left["attempts"]) == ("executing", 1)
    assert (made["idempotency_key"], made["amount_cents"]) == (action_id, 2999)
    assert calls == []
    assert (action["status"], action["attempts"], action["provider_ref"]) == ("succeeded", 2, made["refund_id"])
    assert action["events"][-1]["detail"]["found_on_charge"] is True


def test_a_refund_made_and_not_recorded_on_the_last_attempt_goes_to_a_person_instead(
    client, llm, providers, empty_database, calls
):
    conversation_id, action_id = proposed_refund(client, llm)
    providers.billing.faults.extend(["fail_before", "fail_before", "crash_after"])
    with pytest.raises(SimulatedCrash):
        verdict(client, action_id, "approve")
    left = left_behind(empty_database)
    calls.clear()

    with restart(empty_database, providers) as second:
        action = the_action(second, conversation_id)

    assert (left["status"], left["attempts"]) == ("executing", 3)
    assert providers.billing.get_charge(DUPLICATE, 1)["refund"]["idempotency_key"] == action_id
    assert calls == []
    assert (action["status"], action["attempts"]) == ("needs_review", 3)
    assert action["events"][-1]["type"] == "parked"


def test_a_provider_that_times_out_is_called_again_with_the_same_key_after_each_wait(client, llm, providers, calls):
    _, action_id = proposed_refund(client, llm)
    providers.billing.faults.extend(["slow", "slow"])
    started = time.monotonic()

    approved = verdict(client, action_id, "approve")

    elapsed = time.monotonic() - started
    body = approved.json()
    assert Timing().waits_s == (1.0, 3.0)
    assert (body["status"], body["attempts"]) == ("succeeded", 3)
    assert calls == [("refund", action_id)] * 3
    # both time-outs and both waits were sat through
    assert elapsed >= 2 * SHORT.provider_timeout_s + sum(SHORT.waits_s)
    results = [event["detail"] for event in body["events"] if event["type"] == "provider_result"]
    assert [(result["outcome"], result.get("error")) for result in results] == [
        ("unknown", "timeout"), ("unknown", "timeout"), ("succeeded", None),
    ]
    # each attempt was claimed under a 60 s lease, so the time-outs before it had cleared theirs
    assert [event["detail"]["attempt"] for event in body["events"] if event["type"] == "claimed"] == [1, 2, 3]


def test_three_attempts_without_an_answer_leave_the_refund_in_needs_review_until_a_person_resolves_it(
    client, llm, providers, empty_database, calls
):
    conversation_id, action_id = proposed_refund(client, llm)
    providers.billing.faults.extend(["fail_before"] * 3)

    approved = verdict(client, action_id, "approve")
    parked = approved.json()
    held_while_parked = holders(empty_database)
    resolved = resolve(client, action_id, "failed", note="checked the provider: no refund was made")

    assert (approved.status_code, parked["status"], parked["attempts"]) == (200, "needs_review", 3)
    assert parked["events"][-1]["type"] == "parked"
    assert calls == [("refund", action_id)] * 3
    assert providers.billing.get_charge(DUPLICATE, 1)["state"] == "captured"
    assert [str(holder["id"]) for holder in held_while_parked] == [action_id]
    assert (resolved.status_code, resolved.json()["status"]) == (200, "failed")
    last = the_action(client, conversation_id)["events"][-1]
    assert (last["type"], last["actor"]) == ("resolved", "dana")
    assert last["detail"] == {"outcome": "failed", "note": "checked the provider: no refund was made"}
    assert holders(empty_database) == []


@pytest.mark.parametrize("fault, differs", [("wrong_charge", "charge_id"), ("wrong_amount", "amount_cents")])
def test_an_answer_naming_another_charge_or_amount_leaves_the_refund_in_needs_review_until_a_person_resolves_it(
    client, llm, providers, empty_database, calls, fault, differs
):
    conversation_id, action_id = proposed_refund(client, llm)
    providers.billing.faults.append(fault)

    approved = verdict(client, action_id, "approve")
    parked = approved.json()
    resolved = resolve(client, action_id, "succeeded", note="the provider shows the refund on the right charge")

    assert (approved.status_code, parked["status"], parked["attempts"]) == (200, "needs_review", 1)
    assert calls == [("refund", action_id)]
    mismatch = parked["events"][-1]["detail"]
    assert mismatch["reason"] == "answer_mismatch"
    assert mismatch["answered"][differs] != parked["payload"][differs]
    assert (resolved.status_code, resolved.json()["status"]) == (200, "succeeded")
    assert the_action(client, conversation_id)["events"][-1]["type"] == "resolved"
    # a refund that succeeded keeps its key
    assert [str(holder["id"]) for holder in holders(empty_database)] == [action_id]


def test_a_charge_refunded_by_someone_else_fails_the_refund_and_the_charge_shows_whose_it_was(
    client, llm, providers, empty_database, calls
):
    conversation_id, action_id = proposed_refund(client, llm)
    providers.billing.faults.append("crash_before")
    with pytest.raises(SimulatedCrash):
        verdict(client, action_id, "approve")
    providers.billing.refund(DUPLICATE, amount_cents=2999, currency="USD", idempotency_key="by_hand", timeout_s=1)
    calls.clear()

    with restart(empty_database, providers) as second:
        action = the_action(second, conversation_id)

    assert calls == []
    assert action["status"] == "failed"
    detail = action["events"][-1]["detail"]
    assert (detail["reason"], detail["refund"]["idempotency_key"]) == ("already_refunded", "by_hand")
    assert providers.billing.get_charge(DUPLICATE, 1)["refund"]["idempotency_key"] == "by_hand"


def test_an_incident_call_that_fails_three_times_is_failed_frees_its_key_and_sends_the_ticket_to_a_person(
    client, llm, providers, empty_database, calls
):
    providers.incidents.faults.extend(["fail_before"] * 3)
    llm.script(incident(), answer({**DRAFT, "next_action": "route_to_specialist"}))

    posted = client.post("/tickets", json=request_body("t2", NOW)).json()

    assert len(calls) == 3
    assert len(set(calls)) == 1
    assert providers.incidents.list_open() == []
    action = the_action(client, posted["conversation_id"])
    assert (action["kind"], action["status"], action["attempts"]) == ("incident", "failed", 3)
    assert holders(empty_database) == []
    # the model is told, and the decision goes to a person
    (step,) = tool_steps(client, posted["conversation_id"])
    assert (step["ok"], step["result"]["error"]["type"]) == (False, "incident_not_opened")
    assert posted["decision"]["next_action"] == "escalate_to_human"
    assert rule_names(posted["decision"]) == ["R2"]


@pytest.mark.parametrize("left_as", ["approved", "executing"])
def test_a_turn_that_dies_with_an_incident_unfinished_has_it_failed_at_start_up_and_never_called_again(
    client, llm, providers, empty_database, calls, monkeypatch, left_as
):
    llm.script(incident())
    with monkeypatch.context() as patch:
        if left_as == "approved":
            patch.setattr(action_store, "claim", crash)
        else:
            providers.incidents.faults.append("crash_before")
        with pytest.raises(SimulatedCrash):
            client.post("/tickets", json=request_body("t2", NOW))
    left = left_behind(empty_database)
    (conversation,) = execute(empty_database, "SELECT id FROM conversations")
    calls.clear()

    with restart(empty_database, providers) as second:
        found = second.get(f"/conversations/{conversation['id']}").json()

    assert left["status"] == left_as
    assert calls == []
    assert providers.incidents.list_open() == []
    (action,) = found["actions"]
    assert (action["kind"], action["status"]) == ("incident", "failed")
    assert (action["events"][-1]["type"], action["events"][-1]["detail"]["reason"]) == ("parked", "outcome_unknown")
    assert [(turn["kind"], turn["status"]) for turn in found["turns"]] == [("ingest", "abandoned")]
    (decision,) = found["decisions"]
    assert (decision["source"], decision["fallback_reason"], decision["next_action"]) == (
        "fallback", "turn_abandoned", "escalate_to_human",
    )


def test_a_resolution_is_refused_for_an_unknown_action_an_action_not_in_review_and_an_unknown_outcome(client, llm):
    _, action_id = proposed_refund(client, llm)

    unknown = resolve(client, uuid4(), "succeeded")
    not_in_review = resolve(client, action_id, "succeeded")
    invalid = resolve(client, action_id, "probably")

    assert unknown.status_code == 404
    assert not_in_review.status_code == 409
    assert not_in_review.json()["type"] == "urn:ticket-triage:problem:action-not-in-review"
    assert not_in_review.json()["action_status"] == "pending_approval"
    assert invalid.status_code == 422
    assert verdict(client, action_id, "approve").json()["status"] == "succeeded"
