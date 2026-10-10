import os
from datetime import datetime, timezone

import pytest

from backend.application.actions.registry import EFFECTS
from backend.application.tools.registry import REGISTRY
from evals import score
from evals.cases import BY_ID, CASES
from evals.run import refuse_served, run
from tests.unit.test_rules import DRAFT


def test_the_label_sheet_has_the_twelve_prefix_cases():
    assert [case.id for case in CASES] == [f"T{ticket}-{messages}" for ticket in (1, 2, 3) for messages in (1, 2, 3, 4)]
    first, last = BY_ID["T1-1"], BY_ID["T3-4"]
    assert (first.urgency_accept, first.urgency_forbid) == ({"medium"}, {"critical"})
    assert (first.action_accept, first.action_forbid) == ({"route_to_specialist", "auto_respond"}, set())
    assert (first.required_tools, first.forbidden_effects) == ({"billing_lookup"}, {"incident", "refund"})
    assert (last.action_accept, last.action_forbid) == ({"route_to_specialist"}, {"escalate_to_human"})
    assert {tool for case in CASES for tool in case.required_tools} - set(REGISTRY) == set()


def test_a_value_is_graded_pass_miss_or_violation():
    assert score.grade("high", {"high"}, {"low"}) == "pass"
    assert score.grade("medium", {"high"}, {"low"}) == "miss"
    assert score.grade("low", {"high"}, {"low"}) == "violation"
    assert score.grade(None, {"high"}, {"low"}) == "miss"


def test_an_effect_of_a_forbidden_kind_fails_the_run():
    decision = {**DRAFT, "tools_called": [{"name": "billing_lookup", "ok": True}], "actions": [{"kind": "incident"}]}

    scored = score.score_run(BY_ID["T1-3"], decision)

    assert (scored["urgency"], scored["next_action"]) == ("pass", "pass")
    assert scored["effect_violations"] == ["incident"]
    assert scored["failed"] is True


def test_the_evaluation_refuses_the_served_database(llm):
    with pytest.raises(RuntimeError):
        refuse_served(os.environ["DATABASE_URL"])
    with pytest.raises(RuntimeError):
        run([BY_ID["T1-1"]], runs=1, llm=llm, database_url=os.environ["DATABASE_URL"])
    assert llm.requests == []


def scored_run(effects_proposed, first_call_input_tokens):
    return {
        "urgency": "pass", "next_action": "miss", "tools_missed": ["open_incident"], "effect_violations": [],
        "operator_cited": None, "failed": False, "decision": {"urgency": "high", "next_action": "route_to_specialist"},
        "latency_ms": 3200, "input_tokens": 5000, "output_tokens": 200,
        "effects_proposed": effects_proposed, "first_call_input_tokens": first_call_input_tokens,
    }


def test_every_effect_tool_of_the_registry_is_counted_under_a_registered_effect_kind():
    assert set(score.EFFECT_KIND_BY_TOOL) == {name for name, tool in REGISTRY.items() if tool.is_effect}
    assert set(score.EFFECT_KIND_BY_TOOL.values()) == set(EFFECTS)


def test_the_effect_tool_calls_of_a_turn_are_counted_per_kind_whether_or_not_they_succeeded():
    steps = [
        {"kind": "model", "name": "some-model", "ok": True, "input_tokens": 2732},
        {"kind": "tool", "name": "billing_lookup", "ok": True},
        {"kind": "tool", "name": "propose_refund", "ok": True},
        {"kind": "tool", "name": "propose_refund", "ok": False},
        {"kind": "model", "name": "some-model", "ok": True, "input_tokens": 3100},
        {"kind": "tool", "name": "open_incident", "ok": True},
    ]

    assert score.effects_proposed(steps) == {"refund": 2, "incident": 1}
    assert score.effects_proposed(steps[:2]) == {}


def test_the_first_model_call_of_a_turn_gives_its_input_tokens_and_a_turn_without_one_gives_none():
    steps = [
        {"kind": "model", "name": "some-model", "ok": True, "input_tokens": 2732},
        {"kind": "tool", "name": "billing_lookup", "ok": True},
        {"kind": "model", "name": "some-model", "ok": True, "input_tokens": 3100},
    ]

    assert score.first_call_input_tokens(steps) == 2732
    assert score.first_call_input_tokens(steps[1:2]) is None


def test_a_first_model_call_that_failed_and_was_retried_gives_the_input_tokens_of_the_retry():
    failed = {"kind": "model", "name": "unknown", "ok": False, "input_tokens": None}
    answered = {"kind": "model", "name": "some-model", "ok": True, "input_tokens": 2732}
    later = {"kind": "model", "name": "some-model", "ok": True, "input_tokens": 3100}

    assert score.first_call_input_tokens([failed, answered, later]) == 2732
    assert score.first_call_input_tokens([failed]) is None


def test_the_table_is_printed_with_the_effects_proposed_and_the_first_call_columns_and_the_totals_per_kind():
    agreeing = score.summarize(BY_ID["T1-4"], [scored_run({"refund": 2}, 2992), scored_run({}, 2992), scored_run({"refund": 1}, 2992)])
    differing = score.summarize(BY_ID["T2-2"], [scored_run({}, 2815), scored_run({"incident": 1}, 2807), scored_run({}, None)])
    unknown = score.summarize(BY_ID["T3-1"], [scored_run({}, None)])

    assert agreeing["effects_proposed"] == {"refund": 3}
    assert agreeing["first_call_input_tokens"] == [2992, 2992, 2992]
    assert score.format_table([agreeing, differing, unknown]).splitlines() == [
        "case  urgency p/m/v  action p/m/v  effect violations  tools missed      operator cited  runs differ"
        "  effects proposed  first call in  latency s  tokens in/out",
        "T1-4  3/0/0          0/3/0         -                  open_incident x3  -               no         "
        "  refund x3         2992           3.2        15000/600",
        "T2-2  3/0/0          0/3/0         -                  open_incident x3  -               no         "
        "  incident x1       2807-2815      3.2        15000/600",
        "T3-1  1/0/0          0/1/0         -                  open_incident x1  -               no         "
        "  -                 -              3.2        5000/200",
        "urgency: 7 pass, 0 miss, 0 violation",
        "next_action: 0 pass, 7 miss, 0 violation",
        "failed runs (a forbidden value or a forbidden effect): 0",
        "incident proposed (calls over all runs): 1",
        "refund proposed (calls over all runs): 3",
    ]


def run_with_models(prompt_version, *model_steps):
    steps = [{"kind": "model", "name": name, "ok": is_ok} for name, is_ok in model_steps]
    return {"decision": {"prompt_version": prompt_version}, "conversation": {"turns": [{"steps": steps}]}}


def test_the_header_names_the_prompt_version_the_models_the_runs_and_the_start_in_utc():
    started = datetime(2026, 10, 10, 6, 39, 12, tzinfo=timezone.utc)
    steps = [("gpt-4.1-mini-2025-04-14", True), ("unknown", False)]
    summaries = [{"runs": [run_with_models("triage_v2@fc8d2044", *steps), run_with_models("triage_v2@fc8d2044", *steps)]}]

    assert score.format_header(summaries, "gpt-4.1-mini", 2, started) == (
        "prompt triage_v2@fc8d2044 | model requested gpt-4.1-mini, returned gpt-4.1-mini-2025-04-14"
        " | runs 2 | started 2026-10-10T06:39:12Z"
    )


def test_the_header_lists_every_model_id_returned_and_says_none_when_no_call_was_answered():
    started = datetime(2026, 10, 10, 6, 39, 12, tzinfo=timezone.utc)
    two_ids = [{"runs": [run_with_models("triage_v2@fc8d2044", ("model-b", True)), run_with_models("triage_v2@fc8d2044", ("model-a", True))]}]
    unanswered = [{"runs": [run_with_models("triage_v2@fc8d2044", ("unknown", False))]}]

    assert "returned model-a, model-b |" in score.format_header(two_ids, "model", 1, started)
    assert "returned none |" in score.format_header(unanswered, "model", 1, started)
