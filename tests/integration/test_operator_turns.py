from backend.domain.ports import LLMUnavailable
from backend.infrastructure.llm.scripted import answer, call
from evals import score
from evals.cases import BY_ID
from evals.run import run
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT

QUESTION = "Which of the three charges were captured?"
CORRECTION = "This is medium, not high: the presentation was moved to next week."
ANSWERED = {**DRAFT, "operator_reply": "Two were captured; the third is still a hold."}
CORRECTED = {
    **DRAFT,
    "urgency": "medium",
    "rationale": "The operator said the deadline moved to next week, so urgency is lowered to medium.",
    "operator_reply": "Lowered to medium.",
}


def post_ticket(client):
    return client.post("/tickets", json=request_body("t1", NOW)).json()["conversation_id"]


def say(client, conversation_id, body):
    return client.post(f"/conversations/{conversation_id}/messages", json={"body": body})


def test_two_operator_turns_persist_and_every_decision_version_is_read_back(client, llm):
    llm.script(answer(DRAFT), answer(ANSWERED), answer(CORRECTED))
    conversation_id = post_ticket(client)

    asked = say(client, conversation_id, QUESTION)
    corrected = say(client, conversation_id, CORRECTION)

    assert (asked.status_code, corrected.status_code) == (200, 200)
    assert asked.json()["conversation_id"] == conversation_id
    assert asked.json()["reply"] == "Two were captured; the third is still a hold."
    assert asked.json()["decision"]["version"] == 2
    assert asked.json()["decision"]["changed_fields"] == ["operator_reply"]
    assert corrected.json()["reply"] == "Lowered to medium."
    assert corrected.json()["decision"]["version"] == 3
    assert corrected.json()["decision"]["urgency"] == "medium"
    assert corrected.json()["decision"]["changed_fields"] == ["rationale", "urgency", "operator_reply"]

    conversation = client.get(f"/conversations/{conversation_id}").json()

    assert [decision["version"] for decision in conversation["decisions"]] == [1, 2, 3]
    assert conversation["decisions"][0]["changed_fields"] == []
    assert conversation["decisions"][1:] == [asked.json()["decision"], corrected.json()["decision"]]
    assert [message["author"] for message in conversation["messages"]] == [
        "customer", "customer", "customer", "customer", "operator", "assistant", "operator", "assistant",
    ]
    assert [message["body"] for message in conversation["messages"][4:]] == [
        QUESTION, "Two were captured; the third is still a hold.", CORRECTION, "Lowered to medium.",
    ]
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [
        ("ingest", "completed"), ("operator", "completed"), ("operator", "completed"),
    ]


def test_the_model_is_given_the_operator_conversation_and_the_previous_decision(client, llm):
    llm.script(answer(DRAFT), answer(ANSWERED), answer(CORRECTED))
    conversation_id = post_ticket(client)
    say(client, conversation_id, QUESTION)
    say(client, conversation_id, CORRECTION)

    ingest, _, second = (request["items"][0]["content"] for request in llm.requests)

    thread, after_thread = second.split("</thread>")
    assert "I have THREE charges of $29.99 now" in thread
    assert QUESTION not in thread
    assert CORRECTION not in thread
    assert QUESTION in after_thread
    assert "Two were captured; the third is still a hold." in after_thread
    assert CORRECTION in after_thread
    assert '"version": 2' in after_thread
    assert '"version": 1' not in after_thread
    assert "Conversation with the operator" in ingest
    assert QUESTION not in ingest


def test_an_operator_turn_runs_the_same_tools_and_rules(client, llm):
    unattended = {**ANSWERED, "urgency": "low", "next_action": "auto_respond"}
    llm.script(answer(DRAFT), call("billing_lookup"), answer(unattended))
    conversation_id = post_ticket(client)

    decision = say(client, conversation_id, QUESTION).json()["decision"]

    assert [tool.name for tool in llm.requests[1]["tools"]] == [tool.name for tool in llm.requests[0]["tools"]]
    assert [(tool["name"], tool["ok"]) for tool in decision["tools_called"]] == [("billing_lookup", True)]
    assert decision["next_action"] == "route_to_specialist"
    assert [override["rule"] for override in decision["overrides"]] == ["R3"]
    assert decision["changed_fields"] == ["urgency", "next_action", "operator_reply"]


def test_a_model_failure_on_an_operator_turn_answers_503_and_writes_no_decision(client, llm):
    llm.script(answer(DRAFT), LLMUnavailable("APITimeoutError"), LLMUnavailable("APITimeoutError"))
    conversation_id = post_ticket(client)

    refused = say(client, conversation_id, QUESTION)

    assert refused.status_code == 503
    assert int(refused.headers["Retry-After"]) > 0
    assert "decision" not in refused.json()
    conversation = client.get(f"/conversations/{conversation_id}").json()
    assert [decision["version"] for decision in conversation["decisions"]] == [1]
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [
        ("ingest", "completed"), ("operator", "failed"),
    ]
    assert [step["ok"] for step in conversation["turns"][1]["steps"]] == [False, False]


def test_invalid_output_twice_on_an_operator_turn_answers_503_and_writes_no_decision(client, llm):
    llm.script(answer(DRAFT), answer("not json"), answer("not json"))
    conversation_id = post_ticket(client)

    refused = say(client, conversation_id, QUESTION)

    assert refused.status_code == 503
    assert "Retry-After" in refused.headers
    assert len(client.get(f"/conversations/{conversation_id}").json()["decisions"]) == 1


def test_the_turn_after_a_failed_operator_turn_is_answered(client, llm):
    llm.script(answer(DRAFT), LLMUnavailable("APITimeoutError"), LLMUnavailable("APITimeoutError"), answer(ANSWERED))
    conversation_id = post_ticket(client)
    say(client, conversation_id, QUESTION)

    retried = say(client, conversation_id, QUESTION)

    assert retried.status_code == 200
    assert retried.json()["decision"]["version"] == 2


def test_a_continued_ticket_records_which_fields_changed(client, llm):
    body = {**request_body("t1", NOW), "ticket_id": "T-1"}
    llm.script(answer(DRAFT), answer({**DRAFT, "urgency": "critical"}))

    client.post("/tickets", json={**body, "messages": body["messages"][:3]})
    second = client.post("/tickets", json=body)

    assert second.json()["decision"]["changed_fields"] == ["urgency"]


def test_an_operator_message_to_an_unknown_conversation_is_not_found(client, llm):
    refused = say(client, "00000000-0000-0000-0000-000000000000", QUESTION)

    assert refused.status_code == 404
    assert llm.requests == []


def test_an_empty_operator_message_is_rejected(client, llm):
    llm.script(answer(DRAFT))
    conversation_id = post_ticket(client)

    assert say(client, conversation_id, "").status_code == 422
    assert client.post(f"/conversations/{conversation_id}/messages", json={}).status_code == 422
    assert len(client.get(f"/conversations/{conversation_id}").json()["messages"]) == 4


def run_cases(ids, llm, database_url):
    return run([BY_ID[case_id] for case_id in ids], runs=1, llm=llm, database_url=database_url)


def test_a_question_that_leaves_urgency_and_next_action_alone_passes(empty_database, llm):
    llm.script(answer({**DRAFT, "urgency": "medium"}), answer({**ANSWERED, "urgency": "medium", "sentiment": "mixed"}))

    (summary,) = run_cases(["OP-QUESTION"], llm, empty_database)

    assert summary["urgency"] == {"pass": 1, "miss": 0, "violation": 0}
    assert summary["next_action"] == {"pass": 1, "miss": 0, "violation": 0}
    assert summary["failed_runs"] == 0
    assert summary["runs"][0]["decision"]["version"] == 2
    assert "which are only holds?" in llm.requests[1]["items"][0]["content"].split("</thread>")[1]


def test_a_question_after_which_urgency_differs_fails_the_run(empty_database, llm):
    llm.script(answer(DRAFT), answer({**ANSWERED, "urgency": "critical"}))

    (summary,) = run_cases(["OP-QUESTION"], llm, empty_database)

    assert summary["urgency"] == {"pass": 0, "miss": 0, "violation": 1}
    assert summary["next_action"] == {"pass": 1, "miss": 0, "violation": 0}
    assert summary["failed_runs"] == 1


def test_a_correction_is_scored_on_the_new_urgency_and_on_citing_the_operator(empty_database, llm):
    critical = {**DRAFT, "urgency": "critical"}
    followed = {**DRAFT, "rationale": "The operator corrected urgency to high: one customer is affected.", "operator_reply": "Done."}
    llm.script(answer(critical), answer(followed), answer(critical), answer({**critical, "operator_reply": "It stays."}))

    followed_run, ignored_run = run_cases(["OP-CORRECTION", "OP-CORRECTION"], llm, empty_database)

    assert followed_run["urgency"] == {"pass": 1, "miss": 0, "violation": 0}
    assert (followed_run["operator_cited"], followed_run["failed_runs"]) == (1, 0)
    assert ignored_run["urgency"] == {"pass": 0, "miss": 0, "violation": 1}
    assert (ignored_run["operator_cited"], ignored_run["failed_runs"]) == (0, 1)
    table = score.format_table([followed_run, ignored_run])
    assert "OP-CORRECTION" in table
    assert "operator cited" in table


def test_a_prefix_case_reports_no_operator_citation(empty_database, llm):
    llm.script(answer(DRAFT))

    (summary,) = run_cases(["T1-3"], llm, empty_database)

    assert summary["operator_cited"] is None
