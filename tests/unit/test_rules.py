import itertools
from typing import get_args

import pytest

from backend.application.tools.registry import read_tool_names
from backend.domain import rules
from backend.domain.draft import NextAction, Urgency

ANSWERED = {
    "language": "en",
    "sentiment": "positive",
    "product_area": "app",
    "issue_type": "how_to",
    "additional_issue_types": [],
    "evidence_conflicts": [],
    "rationale": "Message 1 asks about dark mode; the article found by the search answers it.",
    "urgency": "low",
    "next_action": "auto_respond",
    "route_to": None,
    "kb_article_ids": ["kb_dark_mode"],
    "customer_reply_draft": "Choose Settings > Appearance > System Default.",
    "operator_reply": None,
}
# the draft as the rules see it, once the service has added the conversation's actions
ASSEMBLED = {**ANSWERED, "actions": []}
PENDING_REFUND = {"id": "a1", "kind": "refund", "status": "pending_approval"}
CONFLICT = {"source": "kb_search", "claim": "System Default follows the system", "contradicted_by": "message 3"}
# an escalated billing dispute as the model drafts it, shared by the tests of every folder
DRAFT = {
    "language": "en",
    "sentiment": "negative",
    "product_area": "billing",
    "issue_type": "billing_dispute",
    "additional_issue_types": ["account_change"],
    "evidence_conflicts": [
        {
            "source": "billing_lookup",
            "claim": "the upgrade failed and the plan is Free",
            "contradicted_by": "message 3 and the three payment attempts in the same lookup",
        }
    ],
    "rationale": "Messages 3 and 4: three charges and no Pro access, with a deadline in two hours.",
    "urgency": "high",
    "next_action": "escalate_to_human",
    "route_to": "billing",
    "kb_article_ids": [],
    "customer_reply_draft": "We are looking at the three charges now.",
    "operator_reply": None,
}


def tool_step(name, ok):
    return {"kind": "tool", "name": name, "arguments": {}, "result": {}, "ok": ok, "duration_ms": 1}


def test_every_next_action_has_a_rank():
    assert set(rules.NEXT_ACTIONS) == set(get_args(NextAction))
    assert len({action.rank for action in rules.NEXT_ACTIONS.values()}) == len(rules.NEXT_ACTIONS)


def test_no_rule_lowers_the_rank(monkeypatch):
    # beside the real rules, one rule per action that always fires, so a floor below the draft is exercised
    always = tuple(rules.Rule(f"always_{action}", action, lambda decision, steps, incidents: "always") for action in rules.NEXT_ACTIONS)
    for rule_set in (rules.RULES, rules.RULES + always):
        monkeypatch.setattr(rules, "RULES", rule_set)
        _assert_no_combination_is_lowered()


def _assert_no_combination_is_lowered():
    step_lists = [
        [],
        [tool_step("billing_lookup", False)],
        [tool_step("billing_lookup", False), tool_step("billing_lookup", True)],
        [tool_step("kb_search", True), tool_step("status_check", False)],
    ]
    combinations = itertools.product(
        get_args(NextAction), get_args(Urgency), [[], ["kb_dark_mode"]], [[], [CONFLICT]], [None, "A reply."], step_lists,
        [[], [PENDING_REFUND]],
    )
    for action, urgency, articles, conflicts, reply, steps, actions in combinations:
        decision = {
            **ANSWERED,
            "next_action": action,
            "urgency": urgency,
            "kb_article_ids": articles,
            "evidence_conflicts": conflicts,
            "customer_reply_draft": reply,
            "actions": actions,
        }

        final, overrides = rules.apply(decision, steps, read_tools=read_tool_names())

        floors = [rules.rank(override["to"]) for override in overrides]
        assert rules.rank(final) == max([rules.rank(action), *floors])
        assert all(floor > rules.rank(action) for floor in floors)
        assert all(override["from"] == action for override in overrides)


def test_an_answer_backed_by_a_returned_article_goes_out_with_no_person_reading_first():
    assert rules.apply(ASSEMBLED, [tool_step("kb_search", True)], read_tools=read_tool_names()) == ("auto_respond", [])


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"kb_article_ids": []}, "no returned article is cited"),
        ({"evidence_conflicts": [CONFLICT]}, "a conflict is listed"),
        ({"urgency": "high"}, "urgency is high"),
        ({"urgency": "critical"}, "urgency is critical"),
        ({"customer_reply_draft": None}, "there is no reply draft"),
        ({"customer_reply_draft": "  "}, "there is no reply draft"),
    ],
)
def test_an_unattended_answer_missing_one_of_its_conditions_is_routed_to_a_specialist(change, reason):
    final, overrides = rules.apply({**ASSEMBLED, **change}, [], read_tools=read_tool_names())

    assert final == "route_to_specialist"
    assert overrides == [{"rule": "R3", "from": "auto_respond", "to": "route_to_specialist", "reason": reason}]


@pytest.mark.parametrize("action", ["route_to_specialist", "escalate_to_human"])
def test_an_action_a_person_reads_first_is_left_alone(action):
    decision = {**ASSEMBLED, "next_action": action, "kb_article_ids": [], "urgency": "critical"}

    assert rules.apply(decision, [tool_step("billing_lookup", False)], read_tools=read_tool_names()) == (action, [])


def test_a_read_tool_that_failed_last_routes_an_unattended_answer_to_a_specialist():
    final, overrides = rules.apply(ASSEMBLED, [tool_step("kb_search", True), tool_step("billing_lookup", False)], read_tools=read_tool_names())

    assert final == "route_to_specialist"
    assert overrides == [
        {"rule": "R4", "from": "auto_respond", "to": "route_to_specialist", "reason": "billing_lookup failed in this turn"}
    ]


def test_a_read_tool_that_succeeded_after_failing_sets_no_floor():
    steps = [tool_step("billing_lookup", False), tool_step("billing_lookup", True)]

    assert rules.apply(ASSEMBLED, steps, read_tools=read_tool_names()) == ("auto_respond", [])
