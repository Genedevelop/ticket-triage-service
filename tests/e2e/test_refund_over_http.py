from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW, execute
from tests.e2e.conftest import wait_until
from tests.unit.test_rules import DRAFT

TICKET = {**request_body("t1", NOW), "ticket_id": "T-1"}
# a captured charge of the first sample customer
CHARGE = "ch_t1_2"
PROPOSAL = (call("propose_refund", charge_id=CHARGE, reason="charged twice"), answer({**DRAFT, "next_action": "route_to_specialist"}))
APPROVAL = {"verdict": "approve", "approver": "dana", "note": "duplicate charge"}


# the refunds the billing provider made, read from its own table
def refunds_at_the_provider(database_url):
    return execute(database_url, "SELECT charge_id, refund_key, refund_amount_cents FROM mock_charges WHERE refund_id IS NOT NULL")


def event_types(action):
    return [event["type"] for event in action["events"]]


def test_a_proposed_refund_waits_for_a_person_and_an_approval_over_http_refunds_the_charge_once(serve, empty_database):
    server = serve(script=PROPOSAL)

    posted = server.http.post("/tickets", json=TICKET).json()

    (proposed,) = posted["decision"]["actions"]
    assert (proposed["kind"], proposed["status"]) == ("refund", "pending_approval")
    assert posted["decision"]["next_action"] == "escalate_to_human"
    (waiting,) = server.http.get(f"/conversations/{posted['conversation_id']}").json()["actions"]
    assert (waiting["id"], waiting["status"]) == (proposed["id"], "pending_approval")
    assert refunds_at_the_provider(empty_database) == []

    approved = server.http.post(f"/actions/{proposed['id']}/approval", json=APPROVAL)
    approved_again = server.http.post(f"/actions/{proposed['id']}/approval", json=APPROVAL)
    rejected_late = server.http.post(f"/actions/{proposed['id']}/approval", json={**APPROVAL, "verdict": "reject"})

    assert approved.status_code == 200
    assert (approved.json()["status"], approved.json()["attempts"]) == ("succeeded", 1)
    assert event_types(approved.json()) == ["proposed", "approved", "claimed", "provider_result"]
    assert (approved_again.status_code, approved_again.json()["status"]) == (200, "succeeded")
    assert rejected_late.status_code == 409
    assert rejected_late.json()["type"] == "urn:ticket-triage:problem:verdict-contradiction"
    assert refunds_at_the_provider(empty_database) == [
        {"charge_id": CHARGE, "refund_key": proposed["id"], "refund_amount_cents": 2999},
    ]
    (read,) = server.http.get(f"/conversations/{posted['conversation_id']}").json()["actions"]
    assert (read["status"], read["provider_ref"]) == ("succeeded", approved.json()["provider_ref"])


def test_a_server_killed_between_the_approval_and_the_refund_completes_the_refund_exactly_once_when_started_again(serve, empty_database):
    first = serve(script=PROPOSAL, billing_faults=("slow",))
    posted = first.http.post("/tickets", json=TICKET).json()
    (proposed,) = posted["decision"]["actions"]

    with ThreadPoolExecutor(max_workers=1) as background:
        unanswered = background.submit(first.http.post, f"/actions/{proposed['id']}/approval", json=APPROVAL)
        wait_until(
            lambda: execute(empty_database, "SELECT 1 FROM actions WHERE status = 'executing' AND claimed_at IS NOT NULL"),
            "the approved refund to be claimed for execution",
        )
        first.kill()
        with pytest.raises(httpx.TransportError):
            unanswered.result()

    (left,) = execute(empty_database, "SELECT status, attempts FROM actions")
    assert (left["status"], left["attempts"]) == ("executing", 1)
    assert refunds_at_the_provider(empty_database) == []

    second = serve()

    (recovered,) = second.http.get(f"/conversations/{posted['conversation_id']}").json()["actions"]
    assert (recovered["id"], recovered["status"], recovered["attempts"]) == (proposed["id"], "succeeded", 2)
    assert event_types(recovered) == ["proposed", "approved", "claimed", "claimed", "provider_result"]
    assert refunds_at_the_provider(empty_database) == [
        {"charge_id": CHARGE, "refund_key": proposed["id"], "refund_amount_cents": 2999},
    ]
    approved_again = second.http.post(f"/actions/{proposed['id']}/approval", json=APPROVAL)
    assert (approved_again.status_code, approved_again.json()["status"]) == (200, "succeeded")
    assert len(refunds_at_the_provider(empty_database)) == 1
