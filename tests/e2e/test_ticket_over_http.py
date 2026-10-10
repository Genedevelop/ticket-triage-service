import re

from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW
from tests.integration.test_operator_turns import ANSWERED, QUESTION
from tests.unit.test_rules import DRAFT

TICKET = {**request_body("t1", NOW), "ticket_id": "T-1"}
REQUEST_ID = "e2e-request-1"
# the id the service makes up when the caller sent none
GENERATED_ID = re.compile(r"[0-9a-f]{32}")


def test_a_ticket_posted_over_http_is_decided_and_the_same_ticket_again_is_answered_from_store(serve):
    server = serve(script=(call("billing_lookup"), answer(DRAFT)))

    posted = server.http.post("/tickets", json=TICKET, headers={"X-Request-Id": REQUEST_ID})
    replayed = server.http.post("/tickets", json=TICKET)

    assert posted.status_code == 201
    assert posted.headers["content-type"] == "application/json"
    assert posted.headers["X-Request-Id"] == REQUEST_ID
    assert "Idempotent-Replayed" not in posted.headers
    assert set(posted.json()) == {"conversation_id", "decision"}
    decision = posted.json()["decision"]
    assert {key: decision[key] for key in DRAFT} == DRAFT
    assert (decision["version"], decision["source"], decision["model"]) == (1, "model", "scripted-model")
    assert [(tool["name"], tool["ok"]) for tool in decision["tools_called"]] == [("billing_lookup", True)]
    assert replayed.status_code == 200
    assert replayed.headers["Idempotent-Replayed"] == "true"
    assert GENERATED_ID.fullmatch(replayed.headers["X-Request-Id"])
    assert replayed.json() == posted.json()
    read = server.http.get(f"/conversations/{posted.json()['conversation_id']}")
    assert read.status_code == 200
    assert read.json()["decisions"] == [decision]
    logged = [event["event"] for event in server.events() if event["request_id"] == REQUEST_ID]
    assert logged == ["turn.started", "llm.call", "tool.call", "llm.call", "decision.made"]


def test_an_operator_message_over_http_is_answered_with_a_reply_and_a_new_decision_version_once_per_key(serve):
    server = serve(script=(answer(DRAFT), answer(ANSWERED)))
    conversation_id = server.http.post("/tickets", json=TICKET).json()["conversation_id"]
    path = f"/conversations/{conversation_id}/messages"

    asked = server.http.post(path, json={"body": QUESTION}, headers={"Idempotency-Key": "question-1"})
    repeated = server.http.post(path, json={"body": QUESTION}, headers={"Idempotency-Key": "question-1"})

    assert asked.status_code == 200
    assert set(asked.json()) == {"conversation_id", "reply", "decision"}
    assert asked.json()["conversation_id"] == conversation_id
    assert asked.json()["reply"] == ANSWERED["operator_reply"]
    assert (asked.json()["decision"]["version"], asked.json()["decision"]["changed_fields"]) == (2, ["operator_reply"])
    assert repeated.status_code == 200
    assert repeated.json() == asked.json()
    conversation = server.http.get(f"/conversations/{conversation_id}").json()
    assert [message["author"] for message in conversation["messages"]] == ["customer"] * 4 + ["operator", "assistant"]
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [("ingest", "completed"), ("operator", "completed")]
