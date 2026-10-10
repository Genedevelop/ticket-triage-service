import threading
import time
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.application.actions import approve as approval
from backend.application.actions import execute as executor
from backend.application.actions import refund
from backend.domain import action_status as machine
from backend.infrastructure.postgres import actions as store
from tests.conftest import NOW

CUSTOMER = "cus_race"
ROUNDS = 15
LEASE = {"max_attempts": 3, "lease_s": 60}


# the application's pool: each thread takes a connection of its own from it
@pytest.fixture()
def pool(client):
    return client.app.state.pool


# two conversations of one customer
@pytest.fixture()
def conversations(empty_database, client):
    with psycopg.connect(empty_database, autocommit=True) as conn:
        return [
            conn.execute("INSERT INTO conversations (customer_id) VALUES (%s) RETURNING id", (CUSTOMER,)).fetchone()[0]
            for _ in range(2)
        ]


def rows(database_url, query, *parameters):
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        return conn.execute(query, parameters).fetchall()


# run the calls at the same moment, each in its own thread; returns what each returned or raised
def together(*calls):
    barrier = threading.Barrier(len(calls))
    outcomes = [None] * len(calls)

    def run(index, function):
        barrier.wait(timeout=10)
        try:
            outcomes[index] = function()
        except Exception as error:
            outcomes[index] = error

    threads = [threading.Thread(target=run, args=(index, function)) for index, function in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    return outcomes


def charge(providers, number):
    charge_id = f"ch_race_{number}"
    providers.billing.add_charge(charge_id, CUSTOMER, amount_cents=1500, state="captured", created_at=NOW)
    return charge_id


def proposer(pool, providers, conversation_id, charge_id):
    return lambda: refund.propose(
        pool, providers.billing, conversation_id=conversation_id, turn_id=conversation_id,
        customer_id=CUSTOMER, charge_id=charge_id, reason="race", now=NOW, timeout_s=1,
    )


# A proposed refund waiting for a person, and its charge
def pending(pool, providers, conversation_id, number):
    charge_id = charge(providers, number)
    action, _ = proposer(pool, providers, conversation_id, charge_id)()
    return action["id"], charge_id


def decider(pool, providers, action_id, verdict, approver):
    timing = executor.Timing(waits_s=(0, 0))
    return lambda: approval.decide(pool, providers, action_id, verdict=verdict, approver=approver, note=None, timing=timing)


def test_two_proposals_racing_on_the_insert_give_one_action(pool, providers, conversations, empty_database):
    for number in range(ROUNDS):
        charge_id = charge(providers, number)

        (one, one_created), (other, other_created) = together(
            proposer(pool, providers, conversations[0], charge_id), proposer(pool, providers, conversations[1], charge_id)
        )

        assert one["id"] == other["id"]
        assert sorted([one_created, other_created]) == [False, True]
        assert rows(empty_database, "SELECT id FROM actions WHERE key = %s", f"refund:{charge_id}") == [{"id": one["id"]}]
        events = rows(empty_database, "SELECT type, conversation_id FROM action_events WHERE action_id = %s ORDER BY id", one["id"])
        assert [event["type"] for event in events] == ["proposed", "joined"]
        assert {event["conversation_id"] for event in events} == set(conversations)


@pytest.mark.parametrize("first_commits", [True, False])
def test_a_proposal_waits_for_an_uncommitted_holder_of_its_key(pool, conversations, empty_database, first_commits):
    values = ("refund", "refund:ch_held", True, Jsonb({}), Jsonb({}), machine.PENDING)
    proposal = dict(
        kind="refund", key="refund:ch_held", is_key_held=True, status=machine.PENDING, payload={}, evidence={},
        actor="model", conversation_id=conversations[1], detail={"reason": "second"},
    )
    result = []
    with psycopg.connect(empty_database) as first:
        held = first.execute(store.INSERT, values).fetchone()[0]
        second = threading.Thread(target=lambda: result.append(store.propose(pool, **proposal)))
        second.start()
        deadline = time.monotonic() + 10
        waiting = []
        while not waiting and time.monotonic() < deadline:
            waiting = rows(
                empty_database,
                "SELECT pid FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE '%%INSERT INTO actions%%'",
            )
            time.sleep(0.02)
        assert waiting, "the second insert did not wait for the first"
        assert result == []
        if first_commits:
            first.commit()
        else:
            first.rollback()
    second.join(timeout=10)

    ((action, created),) = result
    assert created is not first_commits
    assert (action["id"] == held) is first_commits
    assert rows(empty_database, "SELECT count(*) AS n FROM actions") == [{"n": 1}]


def test_two_approvals_racing_approve_once_and_refund_once(pool, providers, conversations, empty_database, monkeypatch):
    calls = []
    real = providers.billing.refund
    monkeypatch.setattr(
        providers.billing, "refund", lambda charge_id, **parameters: calls.append(charge_id) or real(charge_id, **parameters),
        raising=False,
    )
    for number in range(ROUNDS):
        action_id, charge_id = pending(pool, providers, conversations[0], number)

        outcomes = together(
            decider(pool, providers, action_id, "approve", "dana"), decider(pool, providers, action_id, "approve", "lee")
        )

        assert all(isinstance(outcome, dict) for outcome in outcomes), outcomes
        final = store.get(pool, action_id)
        types = [event["type"] for event in final["events"]]
        assert (final["status"], final["attempts"]) == ("succeeded", 1)
        assert (types.count("approved"), types.count("claimed"), types.count("provider_result")) == (1, 1, 1)
        assert calls.count(charge_id) == 1
        assert providers.billing.get_charge(charge_id, 1)["refund"]["idempotency_key"] == str(action_id)


def test_an_approval_racing_a_rejection_leaves_one_verdict(pool, providers, conversations, empty_database):
    verdicts = set()
    for number in range(ROUNDS):
        action_id, charge_id = pending(pool, providers, conversations[0], number)

        outcomes = together(
            decider(pool, providers, action_id, "approve", "dana"), decider(pool, providers, action_id, "reject", "lee")
        )

        final = store.get(pool, action_id)
        types = [event["type"] for event in final["events"]]
        assert types.count("approved") + types.count("rejected") == 1
        assert [type(outcome) for outcome in outcomes].count(approval.Contradiction) == 1
        refunded = providers.billing.get_charge(charge_id, 1)["state"] == "refunded"
        assert (final["status"], refunded) in {("succeeded", True), ("rejected", False)}
        verdicts.add(final["status"])
    # which verdict wins is up to the race; either way exactly one is recorded
    assert verdicts <= {"succeeded", "rejected"}


def test_a_rejection_that_read_the_action_before_an_approval_was_recorded_reads_it_again_and_is_refused(
    pool, providers, conversations, monkeypatch
):
    action_id, charge_id = pending(pool, providers, conversations[0], 0)
    read = store.get
    seen = []

    def read_then_lose_to_an_approval(given_pool, given_id):
        action = read(given_pool, given_id)
        seen.append(action["status"])
        if len(seen) == 1:
            assert store.decide(pool, action_id, machine.APPROVED, actor="dana")
        return action

    monkeypatch.setattr(store, "get", read_then_lose_to_an_approval)
    with pytest.raises(approval.Contradiction) as refused:
        decider(pool, providers, action_id, "reject", "lee")()
    monkeypatch.undo()

    assert seen == ["pending_approval", "approved"]
    assert refused.value.details == {"action_id": str(action_id)}
    final = store.get(pool, action_id)
    assert [(event["type"], event["actor"]) for event in final["events"]] == [("proposed", "model"), ("approved", "dana")]
    assert providers.billing.get_charge(charge_id, 1)["state"] == "captured"


def test_an_approval_whose_evidence_changed_while_a_rejection_was_recorded_reads_the_action_again_and_is_refused(
    pool, providers, conversations, monkeypatch
):
    action_id, charge_id = pending(pool, providers, conversations[0], 0)
    read_charge = providers.billing.get_charge

    def changed_while_rejected(given_charge_id, timeout_s):
        assert store.decide(pool, action_id, machine.REJECTED, actor="lee", detail={"note": "not a duplicate"})
        return {**read_charge(given_charge_id, timeout_s), "state": "disputed"}

    monkeypatch.setattr(providers.billing, "get_charge", changed_while_rejected, raising=False)
    with pytest.raises(approval.Contradiction):
        decider(pool, providers, action_id, "approve", "dana")()
    monkeypatch.undo()

    final = store.get(pool, action_id)
    assert final["status"] == "rejected"
    assert [(event["type"], event["actor"]) for event in final["events"]] == [("proposed", "model"), ("rejected", "lee")]
    assert providers.billing.get_charge(charge_id, 1)["state"] == "captured"


def test_an_attempt_that_is_no_longer_the_current_one_cannot_release_the_lease(pool, providers, conversations, empty_database):
    action_id, _ = pending(pool, providers, conversations[0], 0)
    store.decide(pool, action_id, machine.APPROVED, actor="dana")
    assert store.claim(pool, action_id, **LEASE)["attempts"] == 1
    assert store.release(pool, action_id, 1, detail={"error": "timeout"})
    assert store.claim(pool, action_id, **LEASE)["attempts"] == 2

    released_late = store.release(pool, action_id, 1, detail={"error": "timeout"})

    assert released_late is False
    assert rows(empty_database, "SELECT status, attempts, claimed_at IS NOT NULL AS held FROM actions WHERE id = %s", action_id) == [
        {"status": "executing", "attempts": 2, "held": True}
    ]
    results = [event["detail"] for event in store.get(pool, action_id)["events"] if event["type"] == "provider_result"]
    assert results == [{"attempt": 1, "outcome": "unknown", "error": "timeout"}]
    assert store.release(pool, action_id, 2, detail={"error": "timeout"})


def test_two_executors_racing_on_the_claim_give_one_claimer(pool, providers, conversations, empty_database):
    for number in range(ROUNDS):
        action_id, _ = pending(pool, providers, conversations[0], number)
        assert store.decide(pool, action_id, machine.APPROVED, actor="dana")

        claims = together(lambda: store.claim(pool, action_id, **LEASE), lambda: store.claim(pool, action_id, **LEASE))

        assert [claim is None for claim in claims].count(True) == 1
        assert rows(empty_database, "SELECT status, attempts FROM actions WHERE id = %s", action_id) == [
            {"status": "executing", "attempts": 1}
        ]


def test_the_claim_the_outcome_write_and_the_parking_behave_as_measured(pool, providers, conversations, empty_database):
    action_id, _ = pending(pool, providers, conversations[0], 0)

    def expire_lease():
        # a lease is expired by moving its row back, never by waiting
        with psycopg.connect(empty_database, autocommit=True) as conn:
            conn.execute("UPDATE actions SET claimed_at = now() - %s WHERE id = %s", (timedelta(seconds=61), action_id))

    def parked():
        return store.park(pool, action_id, machine.NEEDS_REVIEW, **LEASE)

    assert store.claim(pool, action_id, **LEASE) is None
    store.decide(pool, action_id, machine.APPROVED, actor="dana")
    assert store.claim(pool, action_id, **LEASE)["attempts"] == 1
    assert store.claim(pool, action_id, **LEASE) is None
    assert store.release(pool, action_id, 1, detail={"error": "timeout"})
    assert store.claim(pool, action_id, **LEASE)["attempts"] == 2
    assert not store.record_outcome(pool, action_id, 1, machine.SUCCEEDED, provider_ref="late", detail={})
    assert not parked()
    expire_lease()
    assert store.claim(pool, action_id, **LEASE)["attempts"] == 3
    assert not parked()
    expire_lease()
    assert store.claim(pool, action_id, **LEASE) is None
    assert parked()
    assert rows(empty_database, "SELECT status, attempts, provider_ref FROM actions WHERE id = %s", action_id) == [
        {"status": "needs_review", "attempts": 3, "provider_ref": None}
    ]
    assert not store.record_outcome(pool, action_id, 3, machine.SUCCEEDED, provider_ref="late", detail={})


def test_the_live_key_index_releases_and_holds_keys_as_measured(pool, conversations, empty_database):
    def proposed(key, is_key_held=True):
        return store.propose(
            pool, kind="refund", key=key, is_key_held=is_key_held, status=machine.PENDING, payload={}, evidence={},
            actor="model", conversation_id=conversations[0], detail={},
        )

    def left_as(status, is_key_held=True):
        action, _ = proposed(f"key:{status}:{is_key_held}", is_key_held)
        with psycopg.connect(empty_database, autocommit=True) as conn:
            conn.execute("UPDATE actions SET status = %s WHERE id = %s", (status, action["id"]))
        return proposed(f"key:{status}:{is_key_held}", is_key_held)[1]

    assert [left_as(status) for status in ("approved", "executing", "needs_review", "succeeded", "rejected")] == [False] * 5
    assert [left_as(status) for status in ("stale", "failed")] == [True, True]
    assert left_as("succeeded", is_key_held=False) is True
