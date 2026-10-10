from uuid import uuid4

import pytest

from backend.application.actions import approve as approval
from backend.application.actions import execute, registry
from backend.core import errors
from backend.domain import action_status, policy
from backend.infrastructure.postgres import actions as store
from backend.infrastructure.postgres import turns
from tests.conftest import execute as run_sql

CREDIT = "credit"
PAGE = "page"


@pytest.fixture()
def pool(client):
    return client.app.state.pool


@pytest.fixture()
def conversation_id(empty_database, client):
    return run_sql(empty_database, "INSERT INTO conversations (customer_id) VALUES ('cus_registry') RETURNING id")[0]["id"]


# a kind beside the two real ones, in the policy table and the effect registry
def register(monkeypatch, kind, kind_policy, effect):
    monkeypatch.setitem(policy.POLICY, kind, kind_policy)
    monkeypatch.setitem(registry.EFFECTS, kind, effect)


def proposed(pool, conversation_id, kind, status, turn_id=None):
    action, _ = store.propose(
        pool, kind=kind, key=f"{kind}:{uuid4()}", is_key_held=False, status=status, payload={}, evidence={}, actor="model",
        conversation_id=conversation_id, detail={"turn_id": str(turn_id)},
    )
    return action["id"]


def test_start_up_recovery_drives_every_kind_a_person_approves(monkeypatch, pool, providers, conversation_id):
    performed = []

    def perform(action, providers, timeout_s):
        performed.append(action["id"])
        return action_status.SUCCEEDED, "cr_1", {}

    register(monkeypatch, CREDIT, policy.Policy(approver=policy.PERSON, is_key_held=True, parked="needs_review"),
             registry.Effect(perform=perform, changed_evidence_fields=lambda action, providers, timeout_s: []))
    action_id = proposed(pool, conversation_id, CREDIT, action_status.PENDING)
    assert store.decide(pool, action_id, action_status.APPROVED, actor="ana")

    execute.recover_unfinished(pool, providers)

    assert performed == [action_id]
    assert store.get(pool, action_id)["status"] == action_status.SUCCEEDED


def test_an_approval_of_a_kind_with_no_evidence_to_read_again_is_refused_and_approves_nothing(monkeypatch, pool, providers, conversation_id):
    performed = []
    register(monkeypatch, CREDIT, policy.Policy(approver=policy.PERSON, is_key_held=True, parked="needs_review"),
             registry.Effect(perform=lambda action, providers, timeout_s: performed.append(action["id"])))
    action_id = proposed(pool, conversation_id, CREDIT, action_status.PENDING)

    with pytest.raises(approval.EvidenceCheckMissing) as refused:
        approval.decide(pool, providers, action_id, verdict="approve", approver="ana", note=None)

    assert isinstance(refused.value, errors.Internal)
    assert store.get(pool, action_id)["status"] == action_status.PENDING
    assert performed == []


def test_an_approval_of_a_kind_with_no_effect_is_refused_by_name_and_approves_nothing(pool, providers, conversation_id):
    action_id = proposed(pool, conversation_id, CREDIT, action_status.PENDING)

    with pytest.raises(registry.EffectUnregistered) as refused:
        approval.decide(pool, providers, action_id, verdict="approve", approver="ana", note=None)

    assert isinstance(refused.value, errors.Internal)
    assert store.get(pool, action_id)["status"] == action_status.PENDING


def test_the_executor_refuses_a_kind_with_no_effect_by_name_before_claiming_an_attempt(pool, providers, conversation_id):
    action_id = proposed(pool, conversation_id, CREDIT, action_status.APPROVED)

    with pytest.raises(registry.EffectUnregistered):
        execute.drive(pool, providers, action_id)

    left = store.get(pool, action_id)
    assert (left["status"], left["attempts"]) == (action_status.APPROVED, 0)


def test_a_dead_turn_fails_the_unfinished_actions_of_every_kind_its_policy_approves(
    monkeypatch, pool, empty_database, conversation_id
):
    register(monkeypatch, PAGE, policy.Policy(approver="policy:page", is_key_held=False, parked="failed"),
             registry.Effect(perform=lambda action, providers, timeout_s: (action_status.SUCCEEDED, "pg_1", {})))
    turn_id = uuid4()
    run_sql(empty_database, "INSERT INTO turns (id, conversation_id, kind, status) VALUES (%s, %s, 'operator', 'running')", turn_id, conversation_id)
    paged = proposed(pool, conversation_id, PAGE, action_status.APPROVED, turn_id)
    credited = proposed(pool, conversation_id, CREDIT, action_status.APPROVED, turn_id)

    turns.clean_up_dead_turns(pool, lambda steps, actions, previous: {})

    assert store.get(pool, paged)["status"] == action_status.FAILED
    assert store.get(pool, credited)["status"] == action_status.APPROVED
