from backend.application.actions import incident, refund, registry
from backend.domain import policy


def test_every_kind_with_a_policy_row_has_an_effect_and_no_effect_is_without_a_row():
    assert set(registry.EFFECTS) == set(policy.POLICY)
    assert {refund.KIND, incident.KIND} <= set(policy.POLICY)


def test_every_kind_a_person_approves_can_read_its_evidence_again():
    approved_by_a_person = policy.kinds_approved_by_a_person()

    assert refund.KIND in approved_by_a_person
    assert [kind for kind in approved_by_a_person if registry.EFFECTS[kind].changed_evidence_fields is None] == []


def test_the_kinds_split_by_who_approves_them():
    by_a_person, by_their_policy = policy.kinds_approved_by_a_person(), policy.kinds_approved_by_their_policy()

    assert sorted(by_a_person + by_their_policy) == sorted(policy.POLICY)
    assert [kind for kind in by_a_person if policy.POLICY[kind].approver != policy.PERSON] == []
    assert [kind for kind in by_their_policy if policy.POLICY[kind].approver == policy.PERSON] == []
    assert refund.KIND in by_a_person
    assert incident.KIND in by_their_policy
