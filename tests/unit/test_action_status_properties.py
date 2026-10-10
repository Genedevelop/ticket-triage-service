import pytest
from hypothesis import given
from hypothesis import strategies as st

from backend.domain import action_status as machine

KNOWN_STATUSES = st.sampled_from(sorted(machine.STATUSES))
# the statuses of the table, and names it does not have
STATUSES = KNOWN_STATUSES | st.text(max_size=12)
# the final statuses by name, not read from the table, so a table that gives one of them a way out is seen
FINAL_STATUSES = frozenset({"rejected", "stale", "succeeded", "failed"})


def is_accepted(current, following):
    try:
        machine.check(current, following)
    except machine.IllegalTransition:
        return False
    return True


def statuses_reachable_from(status):
    reached, frontier = {status}, [status]
    while frontier:
        for following in machine.TRANSITIONS[frontier.pop()]:
            if following not in reached:
                reached.add(following)
                frontier.append(following)
    return reached


@given(STATUSES, st.lists(STATUSES, max_size=12))
def test_any_sequence_of_steps_moves_only_along_the_table_and_every_other_step_is_refused_where_it_stands(start, attempts):
    current = start
    for following in attempts:
        is_listed = following in machine.TRANSITIONS.get(current, frozenset())

        assert is_accepted(current, following) == is_listed

        if is_listed:
            current = following

    assert current == start or current in machine.STATUSES


@given(st.sampled_from(sorted(FINAL_STATUSES)), st.lists(STATUSES, min_size=1, max_size=12))
def test_no_sequence_of_steps_leaves_a_final_status(final, attempts):
    for following in attempts:
        with pytest.raises(machine.IllegalTransition):
            machine.check(final, following)


@given(st.text(max_size=12).filter(lambda name: name not in machine.STATUSES), STATUSES)
def test_a_status_the_table_does_not_have_leads_nowhere(unknown, following):
    with pytest.raises(machine.IllegalTransition):
        machine.check(unknown, following)


@given(KNOWN_STATUSES)
def test_every_status_that_is_not_final_can_reach_a_final_status_and_a_final_status_reaches_only_itself(status):
    reached = statuses_reachable_from(status)

    assert (status in machine.FINAL) == (status in FINAL_STATUSES)
    if status in FINAL_STATUSES:
        assert reached == {status}
    else:
        assert reached & FINAL_STATUSES

