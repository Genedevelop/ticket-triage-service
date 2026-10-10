import pytest

from backend.core.errors import Internal
from backend.domain import action_status as machine


@pytest.mark.parametrize(
    ("current", "following"),
    [
        (machine.PENDING, machine.APPROVED),
        (machine.PENDING, machine.REJECTED),
        (machine.PENDING, machine.STALE),
        (machine.APPROVED, machine.EXECUTING),
        (machine.EXECUTING, machine.EXECUTING),
        (machine.EXECUTING, machine.NEEDS_REVIEW),
        (machine.NEEDS_REVIEW, machine.SUCCEEDED),
    ],
)
def test_a_step_the_state_machine_lists_is_accepted(current, following):
    assert machine.check(current, following) is None


@pytest.mark.parametrize(
    ("current", "following"),
    [
        (machine.PENDING, machine.SUCCEEDED),
        (machine.PENDING, machine.EXECUTING),
        (machine.REJECTED, machine.APPROVED),
        (machine.SUCCEEDED, machine.FAILED),
        (machine.STALE, machine.APPROVED),
        (machine.NEEDS_REVIEW, machine.EXECUTING),
        ("no_such_state", machine.APPROVED),
    ],
)
def test_a_step_the_state_machine_does_not_list_is_refused_as_an_internal_error_naming_both_statuses(current, following):
    with pytest.raises(machine.IllegalTransition) as refused:
        machine.check(current, following)

    assert isinstance(refused.value, Internal)
    assert refused.value.message == f"{current} -> {following}"


def test_the_final_statuses_are_the_ones_nothing_leads_out_of():
    assert machine.FINAL == {machine.REJECTED, machine.STALE, machine.SUCCEEDED, machine.FAILED}
    assert all(not machine.TRANSITIONS[status] for status in machine.FINAL)
