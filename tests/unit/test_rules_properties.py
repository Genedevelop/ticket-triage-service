import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.application.tools.registry import citable_tool_names, read_tool_names
from backend.domain import action_status, decision, rules
from backend.domain.draft import Draft
from tests.unit.strategies import (
    ACTIONS,
    INCIDENTS,
    TURNS,
    TURNS_ANSWERED_UNATTENDED,
    TURNS_ANSWERED_UNATTENDED_FROM_A_RETURNED_ARTICLE,
)

FLOORS = {rule.name: rule.floor for rule in rules.RULES}
# a rule table of any size: each rule has any floor and either fires or does not
RULE_TABLES = st.lists(st.tuples(st.sampled_from(sorted(rules.NEXT_ACTIONS)), st.booleans()), max_size=6).map(
    lambda rows: tuple(
        rules.Rule(f"T{number}", floor, lambda decision, steps, incidents, fires=fires: "it fires" if fires else None)
        for number, (floor, fires) in enumerate(rows)
    )
)


def decide(draft, steps, actions, incidents):
    return decision.from_draft(
        draft, steps, actions, "scripted-model", "triage_v2@test", incidents,
        read_tools=read_tool_names(), citable_tools=citable_tool_names(),
    )


def has_a_first_answer_that_joined(incident):
    answers = [event["detail"] or {} for event in incident["events"] if event["type"] == "provider_result"]
    return bool(answers) and answers[0].get("already_open") is True


# the rules that must fire for a decision, written from the decision record instead of from the rule functions
def rules_expected_to_fire(draft, decided, steps, actions, incidents):
    expected = set()
    if draft.next_action != "escalate_to_human":
        if any(action["status"] not in action_status.FINAL for action in actions):
            expected.add("R1")
        if any(not has_a_first_answer_that_joined(incident) for incident in incidents):
            expected.add("R2")
    if draft.next_action == "auto_respond":
        is_unsupported = (
            not decided["kb_article_ids"]
            or draft.evidence_conflicts
            or draft.urgency in ("high", "critical")
            or not (draft.customer_reply_draft or "").strip()
        )
        if is_unsupported:
            expected.add("R3")
        last_outcome = {}
        for step in steps:
            if step["kind"] == "tool" and step["name"] in read_tool_names():
                last_outcome[step["name"]] = step["ok"]
        if not all(last_outcome.values()):
            expected.add("R4")
    return expected


@given(TURNS, ACTIONS, INCIDENTS)
def test_the_rules_never_put_a_ticket_further_from_a_person_than_the_draft_did_and_leave_the_urgency_as_drafted(
    turn, actions, incidents
):
    draft, steps = turn
    decided = decide(draft, steps, actions, incidents)

    assert rules.rank(decided["next_action"]) >= rules.rank(draft.next_action)
    assert decided["urgency"] == draft.urgency


@given(TURNS, ACTIONS, INCIDENTS, RULE_TABLES)
def test_no_table_of_rules_whatever_its_floors_lowers_the_drafted_action(turn, actions, incidents, table):
    draft, steps = turn
    drafted = {**draft.model_dump(mode="json"), "actions": actions}

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(rules, "RULES", table)
        final, overrides = rules.apply(drafted, steps, incidents, read_tools=read_tool_names())

    assert rules.rank(final) >= rules.rank(draft.next_action)
    assert all(rules.rank(override["to"]) > rules.rank(override["from"]) for override in overrides)


@given(TURNS, ACTIONS, INCIDENTS)
def test_the_rules_applied_to_their_own_decision_keep_its_action_and_record_no_further_override(turn, actions, incidents):
    draft, steps = turn
    decided = decide(draft, steps, actions, incidents)

    assert rules.apply(decided, steps, incidents, read_tools=read_tool_names()) == (decided["next_action"], [])


@given(TURNS, ACTIONS, INCIDENTS)
def test_a_decision_read_back_as_a_draft_is_decided_the_same_again(turn, actions, incidents):
    draft, steps = turn
    decided = decide(draft, steps, actions, incidents)
    read_back = Draft.model_validate({name: decided[name] for name in Draft.model_fields})

    again = decide(read_back, steps, actions, incidents)

    assert {name: again[name] for name in Draft.model_fields} == {name: decided[name] for name in Draft.model_fields}
    assert again["overrides"] == []


def assert_the_overrides_are_the_rules_that_fired(draft, steps, actions, incidents):
    decided = decide(draft, steps, actions, incidents)
    overrides = decided["overrides"]

    assert {override["rule"] for override in overrides} == rules_expected_to_fire(draft, decided, steps, actions, incidents)
    assert len({override["rule"] for override in overrides}) == len(overrides)
    for override in overrides:
        assert override["from"] == draft.next_action
        assert override["to"] == FLOORS[override["rule"]]
        assert rules.rank(override["to"]) > rules.rank(override["from"])
        assert override["reason"].strip()


@given(TURNS, ACTIONS, INCIDENTS)
def test_the_overrides_recorded_are_exactly_the_rules_that_fired_each_from_the_drafted_action_to_its_own_floor(
    turn, actions, incidents
):
    assert_the_overrides_are_the_rules_that_fired(*turn, actions, incidents)


# twice the examples, because the rule that reads the order of the calls needs one tool called twice to be told apart
@settings(max_examples=200)
@given(TURNS_ANSWERED_UNATTENDED, ACTIONS, INCIDENTS)
def test_for_an_answer_drafted_to_go_out_unattended_the_overrides_recorded_are_exactly_the_rules_that_fired(turn, actions, incidents):
    assert_the_overrides_are_the_rules_that_fired(*turn, actions, incidents)


# twice the examples, because each cause the rule on unsupported answers reads has to come up alone, and none of them too
@settings(max_examples=200)
@given(TURNS_ANSWERED_UNATTENDED_FROM_A_RETURNED_ARTICLE, ACTIONS, INCIDENTS)
def test_for_an_unattended_answer_citing_a_returned_article_the_overrides_recorded_are_exactly_the_rules_that_fired(
    turn, actions, incidents
):
    assert_the_overrides_are_the_rules_that_fired(*turn, actions, incidents)


@given(TURNS, ACTIONS, INCIDENTS)
def test_the_final_action_is_the_highest_floor_among_the_overrides_and_the_drafted_action_when_there_is_none(
    turn, actions, incidents
):
    draft, steps = turn
    decided = decide(draft, steps, actions, incidents)
    candidates = [draft.next_action] + [override["to"] for override in decided["overrides"]]

    assert decided["next_action"] == max(candidates, key=rules.rank)


@given(TURNS, ACTIONS, INCIDENTS)
def test_a_decision_differs_from_its_draft_only_in_the_articles_no_search_returned_and_in_an_overridden_action(
    turn, actions, incidents
):
    draft, steps = turn
    decided = decide(draft, steps, actions, incidents)
    drafted = draft.model_dump(mode="json")
    differing = {name for name in Draft.model_fields if decided[name] != drafted[name]}

    assert differing <= {"kb_article_ids", "next_action"}
    assert ("next_action" in differing) == bool(decided["overrides"])
