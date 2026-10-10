import re
from dataclasses import replace

import pytest

from backend.application.model_input import PROMPT_FILE, load_prompt
from backend.infrastructure.llm.scripted import ScriptedLLM, answer, call
from evals import run as evaluation
from evals import score
from evals.cases import BY_ID
from evals.run import NOW, request_body, run
from tests.unit.test_rules import DRAFT

ROUTED = {**DRAFT, "urgency": "medium", "next_action": "route_to_specialist"}
REFUND = call("propose_refund", charge_id="ch_t1_2", reason="charged twice")
INCIDENT = call("open_incident", component="login", summary="Error 500 at login for every user of one customer")


# a scripted model whose every call reports one hundred input tokens more than the call before it
class MeteredLLM(ScriptedLLM):
    def respond(self, **request):
        return replace(super().respond(**request), input_tokens=100 * len(self.requests))


def test_two_cases_are_scored_pass_miss_and_violation(empty_database, llm):
    llm.script(
        call("billing_lookup"),
        answer(ROUTED),
        answer({**DRAFT, "urgency": "medium", "next_action": "escalate_to_human"}),
    )

    billing, dark_mode = run([BY_ID["T1-1"], BY_ID["T3-3"]], runs=1, llm=llm, database_url=empty_database)

    assert billing["case"] == "T1-1"
    assert billing["urgency"] == {"pass": 1, "miss": 0, "violation": 0}
    assert billing["next_action"] == {"pass": 1, "miss": 0, "violation": 0}
    assert (billing["tools_missed"], billing["effect_violations"], billing["failed_runs"]) == ({}, {}, 0)
    assert dark_mode["urgency"] == {"pass": 0, "miss": 1, "violation": 0}
    assert dark_mode["next_action"] == {"pass": 0, "miss": 0, "violation": 1}
    assert dark_mode["tools_missed"] == {"kb_search": 1}
    assert dark_mode["failed_runs"] == 1


def test_a_case_sends_only_its_prefix_with_the_last_message_arriving_now(empty_database, llm):
    llm.script(answer(ROUTED))

    run([BY_ID["T3-3"]], runs=1, llm=llm, database_url=empty_database)

    thread = llm.requests[0]["items"][0]["content"]
    assert "Switched to 'System Default'" in thread
    assert "schedule dark mode" not in thread
    assert '"age_minutes": 0' in thread
    assert '"age_minutes": 1500' in thread


def test_each_prefix_of_ticket_one_sees_the_billing_state_of_its_moment(empty_database, llm):
    cases = [BY_ID["T1-1"], BY_ID["T1-2"], BY_ID["T1-3"]]
    llm.script(*[step for _ in cases for step in (call("billing_lookup"), answer(ROUTED))])

    summaries = run(cases, runs=1, llm=llm, database_url=empty_database)

    states = []
    for summary in summaries:
        lookup = summary["runs"][0]["conversation"]["turns"][0]["steps"][1]["result"]
        assert lookup["plan"] == "free"
        states.append(sorted(attempt["state"] for attempt in lookup["payment_attempts"]))
    assert states == [["failed"], ["authorized", "authorized"], ["authorized", "captured", "captured"]]


def test_runs_that_differ_are_reported_as_disagreement(empty_database, llm):
    llm.script(answer(ROUTED), answer({**ROUTED, "urgency": "high"}), answer(ROUTED), answer(ROUTED))

    differing, agreeing = run([BY_ID["T1-1"], BY_ID["T1-1"]], runs=2, llm=llm, database_url=empty_database)

    assert differing["urgency"] == {"pass": 1, "miss": 1, "violation": 0}
    assert (differing["disagreement"], agreeing["disagreement"]) == (True, False)
    assert differing["tools_missed"] == {"billing_lookup": 2}


def test_the_table_reports_the_incident_tool_as_missing(empty_database, llm):
    llm.script(call("status_check"), answer({**DRAFT, "urgency": "critical"}))

    summaries = run([BY_ID["T2-2"]], runs=1, llm=llm, database_url=empty_database)

    assert summaries[0]["tools_missed"] == {"open_incident": 1}
    assert summaries[0]["failed_runs"] == 0
    table = score.format_table(summaries)
    assert "T2-2" in table
    assert "open_incident" in table


def test_the_effect_tool_calls_of_each_run_are_counted_per_kind_and_shown_in_the_table(empty_database, llm):
    llm.script(
        call("billing_lookup"), REFUND, REFUND, answer(DRAFT),
        call("billing_lookup"), answer(DRAFT),
        call("status_check"), INCIDENT, answer({**DRAFT, "urgency": "critical"}),
    )

    summaries = run([BY_ID["T1-4"], BY_ID["T1-4"], BY_ID["T2-3"]], runs=1, llm=llm, database_url=empty_database)

    assert [summary["effects_proposed"] for summary in summaries] == [{"refund": 2}, {}, {"incident": 1}]
    assert [summary["runs"][0]["effects_proposed"] for summary in summaries] == [{"refund": 2}, {}, {"incident": 1}]
    table = score.format_table(summaries).splitlines()
    assert "effects proposed" in table[0]
    assert ["refund x2" in table[1], "refund" in table[2], "incident x1" in table[3]] == [True, False, True]
    assert table[-2:] == ["incident proposed (calls over all runs): 1", "refund proposed (calls over all runs): 2"]


def test_the_first_call_column_is_the_input_tokens_of_the_first_model_call_of_each_run(empty_database):
    llm = MeteredLLM()
    llm.script(call("billing_lookup"), answer(ROUTED), answer(ROUTED), call("billing_lookup"), answer(ROUTED))

    (differing,) = run([BY_ID["T1-1"]], runs=2, llm=llm, database_url=empty_database)
    (single,) = run([BY_ID["T1-1"]], runs=1, llm=llm, database_url=empty_database)

    assert differing["first_call_input_tokens"] == [100, 300]
    assert differing["input_tokens"] == 100 + 200 + 300
    assert single["first_call_input_tokens"] == [400]
    table = score.format_table([differing, single]).splitlines()
    assert "first call in" in table[0]
    assert ("100-300" in table[1], " 400 " in table[2]) == (True, True)


def test_an_operator_case_counts_only_the_effects_and_the_first_call_of_the_scored_turn(empty_database):
    llm = MeteredLLM()
    critical = {**DRAFT, "urgency": "critical"}
    llm.script(call("billing_lookup"), REFUND, answer(critical), INCIDENT, answer({**DRAFT, "operator_reply": "Done."}))

    (summary,) = run([BY_ID["OP-CORRECTION"]], runs=1, llm=llm, database_url=empty_database)

    assert summary["effects_proposed"] == {"incident": 1}
    assert summary["first_call_input_tokens"] == [400]
    assert summary["input_tokens"] == 100 + 200 + 300 + 400 + 500


# the command with the scripted model in place of the vendor's and the test database in place of its own
def run_command(monkeypatch, llm, database_url, *arguments):
    monkeypatch.setenv("TRIAGE_MODEL", "requested-model")
    monkeypatch.setattr(evaluation, "OpenAIResponsesLLM", lambda *settings: llm)
    monkeypatch.setattr(evaluation, "recreate_database", lambda served_url: database_url)
    return evaluation.main(list(arguments))


def test_the_command_prints_one_header_line_before_the_table_and_exits_with_zero(empty_database, llm, monkeypatch, capsys):
    llm.script(call("billing_lookup"), answer(ROUTED), call("billing_lookup"), answer(ROUTED))

    exit_code = run_command(monkeypatch, llm, empty_database, "--case", "T1-1", "--runs", "2")

    header, columns, row = [line for line in capsys.readouterr().out.splitlines() if not line.startswith("{")][:3]
    assert exit_code == 0
    assert re.fullmatch(
        r"prompt triage_v2@[0-9a-f]{8} \| model requested requested-model, returned scripted-model"
        r" \| runs 2 \| started \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        header,
    )
    assert columns.startswith("case  urgency p/m/v")
    assert row.startswith("T1-1  2/0/0")


def test_the_command_exits_with_one_when_a_run_fails_its_labels(empty_database, llm, monkeypatch, capsys):
    llm.script(answer({**DRAFT, "urgency": "critical"}))

    exit_code = run_command(monkeypatch, llm, empty_database, "--case", "T1-1", "--runs", "1")

    assert exit_code == 1
    assert "failed runs (a forbidden value or a forbidden effect): 1" in capsys.readouterr().out


def test_a_prompt_passed_to_the_evaluation_is_the_text_sent_and_the_version_stored(empty_database, llm):
    first_version = load_prompt(PROMPT_FILE.with_name("triage_v1.md"))
    llm.script(answer(ROUTED), answer(ROUTED))

    (measured,) = run([BY_ID["T1-1"]], runs=1, llm=llm, database_url=empty_database, prompt=first_version)
    (shipped,) = run([BY_ID["T1-1"]], runs=1, llm=llm, database_url=empty_database)

    assert first_version.version.startswith("triage_v1@")
    assert measured["runs"][0]["decision"]["prompt_version"] == first_version.version
    assert shipped["runs"][0]["decision"]["prompt_version"] == load_prompt().version
    assert [request["instructions"] for request in llm.requests] == [first_version.text, load_prompt().text]


def test_the_command_evaluates_the_prompt_file_named_by_its_stem(empty_database, llm, monkeypatch, capsys):
    llm.script(answer(ROUTED))

    exit_code = run_command(monkeypatch, llm, empty_database, "--case", "T1-1", "--runs", "1", "--prompt", "triage_v1")

    assert exit_code == 0
    assert f"prompt {load_prompt(PROMPT_FILE.with_name('triage_v1.md')).version} | " in capsys.readouterr().out
    assert llm.requests[0]["instructions"] == load_prompt(PROMPT_FILE.with_name("triage_v1.md")).text


def test_the_command_refuses_an_unknown_prompt_with_the_usage_code_and_lists_the_stems(empty_database, llm, monkeypatch, capsys):
    with pytest.raises(SystemExit) as refused:
        run_command(monkeypatch, llm, empty_database, "--prompt", "triage_v0")

    usage = capsys.readouterr().err
    assert refused.value.code == 2
    assert "triage_v0" in usage
    assert [stem for stem in evaluation.prompt_stems() if stem not in usage] == []
    assert {"triage_v1", "triage_v2"} <= set(evaluation.prompt_stems())
    assert llm.requests == []


def test_the_drafted_third_prompt_is_evaluated_by_name_while_a_posted_ticket_still_gets_the_shipped_one(
    empty_database, llm, monkeypatch, capsys, client
):
    third = load_prompt(PROMPT_FILE.with_name("triage_v3.md"))
    llm.script(answer(ROUTED), answer(ROUTED))

    exit_code = run_command(monkeypatch, llm, empty_database, "--case", "T1-1", "--runs", "1", "--prompt", "triage_v3")
    served = client.post("/tickets", json=request_body(BY_ID["T1-1"], NOW)).json()["decision"]

    assert exit_code == 0
    assert "prompt triage_v3@3df4a585 | " in capsys.readouterr().out
    assert llm.requests[0]["instructions"] == third.text
    assert served["prompt_version"] == load_prompt().version
    assert llm.requests[1]["instructions"] == load_prompt().text
