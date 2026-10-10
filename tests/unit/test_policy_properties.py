from hypothesis import given
from hypothesis import strategies as st

from backend.domain import policy

# the kinds of the table, and any other text
KINDS = st.sampled_from(sorted(policy.POLICY)) | st.text()


@given(KINDS)
def test_every_kind_whatever_its_name_has_a_policy_row(kind):
    row = policy.policy_for(kind)

    assert isinstance(row, policy.Policy)
    assert row.approver and row.parked and row.max_attempts >= 1


@given(st.text().filter(lambda kind: kind not in policy.POLICY))
def test_a_kind_the_table_does_not_list_needs_a_person_and_keeps_its_key(kind):
    row = policy.policy_for(kind)

    assert row.approver == policy.PERSON
    assert row.is_key_held
    assert row.parked == "needs_review"


@given(KINDS)
def test_a_kind_is_approved_by_a_person_or_by_its_own_policy_row_and_never_by_both(kind):
    is_approved_by_a_person = policy.policy_for(kind).approver == policy.PERSON
    is_approved_by_its_row = kind in policy.kinds_approved_by_their_policy()

    assert is_approved_by_a_person != is_approved_by_its_row
    assert (kind in policy.kinds_approved_by_a_person()) == (is_approved_by_a_person and kind in policy.POLICY)
