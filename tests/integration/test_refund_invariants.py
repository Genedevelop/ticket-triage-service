import psycopg
import pytest
from psycopg.rows import dict_row

from backend.domain.ports import ProviderError
from backend.infrastructure.llm.scripted import answer, call
from backend.infrastructure.postgres import billing_fixtures
from backend.infrastructure.providers.sample_data import THREADS
from evals.sample import request_body
from tests.conftest import NOW, execute
from tests.unit.test_rules import ANSWERED, DRAFT

CUSTOMER = THREADS["t1"]["customer"]["id"]
# the seeded charges of the first sample customer: two captured payments and a hold
CAPTURED, DUPLICATE, HOLD = "ch_t1_1", "ch_t1_2", "ch_t1_3"
ROUTED = {**DRAFT, "next_action": "route_to_specialist"}


def propose(charge_id, reason="charged twice"):
    return call("propose_refund", charge_id=charge_id, reason=reason)


def post_ticket(client, llm, *steps, draft=ROUTED):
    llm.script(*steps, answer(draft))
    posted = client.post("/tickets", json=request_body("t1", NOW))
    assert posted.status_code == 201
    return posted.json()


def results(client, conversation_id, turn=-1):
    turns = client.get(f"/conversations/{conversation_id}").json()["turns"]
    return [step["result"] for step in turns[turn]["steps"] if step["name"] == "propose_refund"]


def verdict(client, action_id, verdict, approver="dana", note=None):
    return client.post(f"/actions/{action_id}/approval", json={"verdict": verdict, "approver": approver, "note": note})


def rows(database_url, query, *parameters):
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        return conn.execute(query, parameters).fetchall()


# every call that reaches the provider's refund method
@pytest.fixture()
def refund_calls(providers, monkeypatch):
    calls = []
    real = providers.billing.refund

    def counted(charge_id, **parameters):
        calls.append(charge_id)
        return real(charge_id, **parameters)

    monkeypatch.setattr(providers.billing, "refund", counted, raising=False)
    return calls


def assert_every_provider_refund_follows_an_approval(database_url):
    for charge in rows(database_url, "SELECT * FROM mock_charges WHERE state = 'refunded'"):
        events = rows(
            database_url, "SELECT type FROM action_events WHERE action_id = %s::uuid ORDER BY id", charge["refund_key"]
        )
        types = [event["type"] for event in events]
        assert "approved" in types, f"{charge['charge_id']} was refunded without an approval"
        assert types.index("approved") < types.index("claimed") < types.index("provider_result")


def test_a_model_that_proposes_everything_moves_no_money(client, llm, providers, empty_database, refund_calls):
    posted = post_ticket(
        client, llm, propose(CAPTURED), propose(DUPLICATE), propose(HOLD), propose(CAPTURED), propose("ch_unknown"),
        draft={**ANSWERED, "next_action": "auto_respond"},
    )

    assert refund_calls == []
    assert [providers.billing.get_charge(charge, 1)["state"] for charge in (CAPTURED, DUPLICATE, HOLD)] == [
        "captured", "captured", "authorized",
    ]
    decision = posted["decision"]
    assert [(action["kind"], action["status"]) for action in decision["actions"]] == [("refund", "pending_approval")] * 2
    assert decision["next_action"] == "escalate_to_human"
    assert [override["rule"] for override in decision["overrides"]][0] == "R1"
    assert rows(empty_database, "SELECT count(*) AS n FROM action_events WHERE type IN ('approved', 'claimed')") == [{"n": 0}]
    assert_every_provider_refund_follows_an_approval(empty_database)


def test_an_approved_refund_is_executed_once_with_the_charged_amount(client, llm, providers, empty_database, refund_calls):
    posted = post_ticket(client, llm, propose(DUPLICATE))
    (action,) = posted["decision"]["actions"]

    approved = verdict(client, action["id"], "approve", note="duplicate of ch_t1_1")

    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "succeeded"
    assert body["attempts"] == 1
    assert body["payload"] == {"charge_id": DUPLICATE, "amount_cents": 2999, "currency": "USD"}
    assert body["evidence"]["customer_id"] == CUSTOMER
    assert [event["type"] for event in body["events"]] == ["proposed", "approved", "claimed", "provider_result"]
    assert body["events"][1]["actor"] == "dana"
    charge = providers.billing.get_charge(DUPLICATE, 1)
    assert charge["state"] == "refunded"
    assert charge["refund"] == {"refund_id": body["provider_ref"], "idempotency_key": action["id"], "amount_cents": 2999}
    assert providers.billing.get_charge(CAPTURED, 1)["state"] == "captured"
    assert refund_calls == [DUPLICATE]
    assert_every_provider_refund_follows_an_approval(empty_database)


def test_at_most_one_refund_succeeds_per_charge(client, llm, providers, empty_database, refund_calls):
    first = post_ticket(client, llm, propose(DUPLICATE))
    second = post_ticket(client, llm, propose(DUPLICATE))
    (action,) = first["decision"]["actions"]
    assert second["conversation_id"] != first["conversation_id"]
    assert second["decision"]["actions"] == first["decision"]["actions"]

    answers = [verdict(client, action["id"], "approve", approver=name) for name in ("dana", "dana", "lee")]
    llm.script(propose(DUPLICATE), answer(ROUTED))
    again = client.post(f"/conversations/{first['conversation_id']}/messages", json={"body": "Refund it once more."})

    assert [(answered.status_code, answered.json()["status"]) for answered in answers] == [(200, "succeeded")] * 3
    assert refund_calls == [DUPLICATE]
    assert results(client, first["conversation_id"])[0]["reason"] == "charge_refunded"
    assert rows(empty_database, "SELECT status, attempts FROM actions WHERE key = %s", f"refund:{DUPLICATE}") == [
        {"status": "succeeded", "attempts": 1}
    ]
    assert again.json()["decision"]["actions"] == [{"id": action["id"], "kind": "refund", "status": "succeeded"}]
    assert again.json()["decision"]["overrides"] == []
    assert_every_provider_refund_follows_an_approval(empty_database)


def test_another_customers_charge_is_denied_exactly_like_one_that_does_not_exist(client, llm, providers, empty_database):
    providers.billing.add_charge("ch_other", "cus_someone_else", amount_cents=2999, state="captured", created_at=NOW)

    posted = post_ticket(client, llm, propose("ch_other"), propose("ch_missing"))

    theirs, missing = results(client, posted["conversation_id"])
    assert theirs == missing == {"denied": True, "reason": "charge_not_found", "message": "this customer has no such charge"}
    steps = client.get(f"/conversations/{posted['conversation_id']}").json()["turns"][0]["steps"]
    assert [step["ok"] for step in steps if step["name"] == "propose_refund"] == [True, True]
    assert posted["decision"]["actions"] == []
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 0}]
    assert rows(empty_database, "SELECT count(*) AS n FROM action_events") == [{"n": 0}]


def test_a_ticket_without_a_customer_cannot_propose_a_refund(client, llm, empty_database):
    body = request_body("t1", NOW)
    del body["customer"]
    llm.script(propose(CAPTURED), answer(ROUTED))

    posted = client.post("/tickets", json=body).json()

    (result,) = results(client, posted["conversation_id"])
    assert result["error"]["type"] == "customer_unknown"
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 0}]


@pytest.mark.parametrize(
    "charge_id, state, reason, words",
    [
        (HOLD, "authorized", "not_refundable", "needs a void, which this service does not do"),
        ("ch_failed", "failed", "charge_failed", "the charge is failed"),
        ("ch_refunded", "refunded", "charge_refunded", "the charge is refunded"),
    ],
)
def test_only_a_captured_charge_can_be_proposed(client, llm, providers, empty_database, charge_id, state, reason, words):
    providers.billing.add_charge(charge_id, CUSTOMER, amount_cents=2999, state=state, created_at=NOW)

    posted = post_ticket(client, llm, propose(charge_id))

    (result,) = results(client, posted["conversation_id"])
    assert (result["denied"], result["reason"]) == (True, reason)
    assert words in result["message"]
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 0}]


def test_the_tool_called_twice_gives_one_action(client, llm, empty_database):
    posted = post_ticket(client, llm, propose(CAPTURED, "first reason"), propose(CAPTURED, "another reason"))

    first, second = results(client, posted["conversation_id"])
    assert first["action"] == {
        "id": first["action"]["id"], "kind": "refund", "status": "pending_approval",
        "charge_id": CAPTURED, "amount_cents": 2999, "currency": "USD",
    }
    assert (first["already_proposed"], second["already_proposed"]) == (False, True)
    assert second["action"] == first["action"]
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 1}]
    conversation = client.get(f"/conversations/{posted['conversation_id']}").json()
    (action,) = conversation["actions"]
    assert [(event["type"], event["actor"], event["detail"]["reason"]) for event in action["events"]] == [
        ("proposed", "model", "first reason"), ("joined", "model", "another reason"),
    ]
    assert {event["conversation_id"] for event in action["events"]} == {posted["conversation_id"]}
    assert posted["decision"]["actions"] == [{"id": action["id"], "kind": "refund", "status": "pending_approval"}]


def test_a_persons_rejection_stays(client, llm, providers, empty_database, refund_calls):
    posted = post_ticket(client, llm, propose(CAPTURED))
    (action,) = posted["decision"]["actions"]

    rejected = verdict(client, action["id"], "reject", note="this charge pays for Pro")
    repeated = verdict(client, action["id"], "reject", approver="lee")
    opposite = verdict(client, action["id"], "approve")
    llm.script(propose(CAPTURED), answer(ROUTED))
    again = client.post(f"/conversations/{posted['conversation_id']}/messages", json={"body": "Propose it again."})
    elsewhere = post_ticket(client, llm, propose(CAPTURED))

    assert (rejected.status_code, rejected.json()["status"]) == (200, "rejected")
    assert (repeated.status_code, repeated.json()["status"]) == (200, "rejected")
    assert opposite.status_code == 409
    for conversation_id in (posted["conversation_id"], elsewhere["conversation_id"]):
        result = results(client, conversation_id)[0]
        assert (result["denied"], result["reason"]) == (True, "rejected_by_a_person")
        assert result["message"] == "this refund was rejected by dana: this charge pays for Pro"
        assert result["action"] == {"id": action["id"], "kind": "refund", "status": "rejected"}
    assert rows(empty_database, "SELECT status FROM actions") == [{"status": "rejected"}]
    assert rows(empty_database, "SELECT count(*) AS n FROM action_events WHERE type = 'rejected'") == [{"n": 1}]
    # a rejected action is finished, so it no longer holds the ticket in front of a person
    assert again.json()["decision"]["actions"] == [{"id": action["id"], "kind": "refund", "status": "rejected"}]
    assert again.json()["decision"]["next_action"] == "route_to_specialist"
    assert refund_calls == []
    assert providers.billing.get_charge(CAPTURED, 1)["state"] == "captured"


def test_a_rejection_after_an_approval_is_refused(client, llm):
    (action,) = post_ticket(client, llm, propose(CAPTURED))["decision"]["actions"]
    verdict(client, action["id"], "approve")

    assert verdict(client, action["id"], "reject").status_code == 409


def test_a_charge_that_changed_since_the_proposal_makes_the_approval_stale(client, llm, providers, empty_database, refund_calls):
    (action,) = post_ticket(client, llm, propose(CAPTURED))["decision"]["actions"]
    providers.billing.refund(CAPTURED, amount_cents=2999, currency="USD", idempotency_key="by-hand", timeout_s=1)
    refund_calls.clear()

    approved = verdict(client, action["id"], "approve")

    assert (approved.status_code, approved.json()["status"]) == (200, "stale")
    assert approved.json()["events"][-1]["type"] == "stale"
    assert set(approved.json()["events"][-1]["detail"]["changed"]) == {"state", "refund"}
    assert refund_calls == []
    assert providers.billing.get_charge(CAPTURED, 1)["refund"]["idempotency_key"] == "by-hand"


def test_an_approval_with_unreadable_evidence_answers_503_and_stays_pending(client, llm, providers, monkeypatch, refund_calls):
    (action,) = post_ticket(client, llm, propose(CAPTURED))["decision"]["actions"]
    readable = providers.billing.get_charge

    def unreadable(charge_id, timeout_s):
        raise ProviderError("timeout", "the billing system did not answer", is_retryable=True)

    monkeypatch.setattr(providers.billing, "get_charge", unreadable, raising=False)
    refused = verdict(client, action["id"], "approve")
    monkeypatch.setattr(providers.billing, "get_charge", readable, raising=False)
    approved = verdict(client, action["id"], "approve")

    assert refused.status_code == 503
    assert refused.headers["Retry-After"]
    assert (approved.status_code, approved.json()["status"]) == (200, "succeeded")
    assert [event["type"] for event in approved.json()["events"]].count("approved") == 1
    assert refund_calls == [CAPTURED]


def test_a_charge_that_no_longer_exists_makes_the_approval_stale_and_every_later_verdict_is_answered_with_the_stale_action(
    client, llm, empty_database, refund_calls
):
    (action,) = post_ticket(client, llm, propose(CAPTURED))["decision"]["actions"]
    execute(empty_database, "DELETE FROM mock_charges WHERE charge_id = %s", CAPTURED)

    approved = verdict(client, action["id"], "approve")
    approved_again = verdict(client, action["id"], "approve")
    rejected_late = verdict(client, action["id"], "reject")

    assert (approved.status_code, approved.json()["status"]) == (200, "stale")
    assert approved.json()["events"][-1]["detail"] == {"changed": ["charge"]}
    assert [event["type"] for event in approved.json()["events"]] == ["proposed", "stale"]
    assert (approved_again.status_code, rejected_late.status_code) == (200, 200)
    assert approved_again.json() == rejected_late.json() == approved.json()
    assert refund_calls == []


def test_a_billing_system_that_does_not_answer_the_proposal_is_told_to_the_model_as_a_tool_error_and_nothing_is_proposed(
    client, llm, providers, monkeypatch, empty_database
):
    def unanswered(charge_id, timeout_s):
        raise ProviderError("timeout", "the billing system did not answer", is_retryable=True)

    monkeypatch.setattr(providers.billing, "get_charge", unanswered, raising=False)

    posted = post_ticket(client, llm, propose(CAPTURED))

    (result,) = results(client, posted["conversation_id"])
    assert result == {"error": {"type": "timeout", "message": "the billing system did not answer", "retryable": True}}
    assert [(tool["name"], tool["ok"]) for tool in posted["decision"]["tools_called"]] == [("propose_refund", False)]
    assert posted["decision"]["actions"] == []
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 0}]


def test_an_approval_is_validated_and_an_unknown_action_is_not_found(client, llm):
    (action,) = post_ticket(client, llm, propose(CAPTURED))["decision"]["actions"]
    url = f"/actions/{action['id']}/approval"

    assert client.post(url, json={"verdict": "maybe", "approver": "dana"}).status_code == 422
    assert client.post(url, json={"verdict": "approve", "approver": ""}).status_code == 422
    assert client.post("/actions/not-a-uuid/approval", json={"verdict": "approve", "approver": "dana"}).status_code == 422
    missing = "/actions/00000000-0000-0000-0000-000000000000/approval"
    assert client.post(missing, json={"verdict": "approve", "approver": "dana"}).status_code == 404


def test_the_mock_refund_follows_an_idempotency_key_contract(providers):
    billing = providers.billing
    for charge_id, state in (("ch_a", "captured"), ("ch_b", "captured"), ("ch_hold", "authorized")):
        billing.add_charge(charge_id, CUSTOMER, amount_cents=500, state=state, created_at=NOW)

    def refund(charge_id, key, amount_cents=500):
        return billing.refund(charge_id, amount_cents=amount_cents, currency="USD", idempotency_key=key, timeout_s=1)

    def refusal(charge_id, key, amount_cents=500):
        with pytest.raises(ProviderError) as refused:
            refund(charge_id, key, amount_cents)
        return refused.value.error_type, refused.value.is_retryable

    made = refund("ch_a", "key-1")

    assert made == {"refund_id": made["refund_id"], "charge_id": "ch_a", "amount_cents": 500, "currency": "USD", "idempotency_key": "key-1"}
    assert refund("ch_a", "key-1") == made
    assert refusal("ch_b", "key-1") == ("idempotency_key_reuse", False)
    assert refusal("ch_a", "key-2") == ("already_refunded", False)
    assert refusal("ch_hold", "key-3") == ("not_refundable", False)
    assert refusal("ch_b", "key-4", amount_cents=100) == ("amount_mismatch", False)
    assert refusal("ch_none", "key-5") == ("charge_not_found", False)
    assert billing.get_charge("ch_a", 1)["refund"] == {"refund_id": made["refund_id"], "idempotency_key": "key-1", "amount_cents": 500}
    assert billing.get_charge("ch_b", 1) == {
        "charge_id": "ch_b", "customer_id": CUSTOMER, "amount_cents": 500, "currency": "USD", "state": "captured", "refund": None,
    }


@pytest.mark.parametrize("winning_key", ["key-1", "key-2"])
def test_the_mock_answers_a_refund_that_lost_the_charge_between_its_read_and_its_update_only_under_the_winning_key(
    providers, monkeypatch, winning_key
):
    providers.billing.add_charge("ch_a", CUSTOMER, amount_cents=500, state="captured", created_at=NOW)
    read = billing_fixtures.get_charge
    won = []

    def read_then_lose_the_charge(pool, charge_id):
        row = read(pool, charge_id)
        if not won:
            won.append(billing_fixtures.refund_charge(pool, charge_id=charge_id, refund_id="re_winner", idempotency_key=winning_key))
        return row

    monkeypatch.setattr(billing_fixtures, "get_charge", read_then_lose_the_charge)

    def refund():
        return providers.billing.refund("ch_a", amount_cents=500, currency="USD", idempotency_key="key-1", timeout_s=1)

    if winning_key == "key-1":
        assert refund() == {"refund_id": "re_winner", "charge_id": "ch_a", "amount_cents": 500, "currency": "USD", "idempotency_key": "key-1"}
    else:
        with pytest.raises(ProviderError) as refused:
            refund()
        assert (refused.value.error_type, refused.value.is_retryable) == ("already_refunded", False)
    assert providers.billing.get_charge("ch_a", 1)["refund"] == {"refund_id": "re_winner", "idempotency_key": winning_key, "amount_cents": 500}


def test_the_model_is_given_the_proposed_effects_and_their_status(client, llm):
    posted = post_ticket(client, llm, propose(CAPTURED))
    (action,) = posted["decision"]["actions"]
    llm.script(answer(ROUTED))
    client.post(f"/conversations/{posted['conversation_id']}/messages", json={"body": "What is waiting?"})

    first, second = llm.requests[0]["items"][0]["content"], llm.requests[-1]["items"][0]["content"]

    assert "Effects proposed for this ticket and their status now:\nnone" in first
    effects = second.split("Effects proposed for this ticket and their status now:")[1].split("Conversation with the operator")[0]
    assert action["id"] in effects and '"status": "pending_approval"' in effects and CAPTURED in effects
