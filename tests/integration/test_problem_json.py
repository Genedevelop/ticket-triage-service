from backend.domain.ports import LLMMisconfigured, LLMUnavailable
from backend.infrastructure.llm.scripted import answer
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT

PROBLEM = "application/problem+json"
MISSING = "00000000-0000-0000-0000-000000000000"
REJECTED = "AuthenticationError 401 invalid_api_key"


def assert_problem(response, status, type_suffix, title):
    body = response.json()
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM
    assert body["status"] == status
    assert body["type"].endswith(type_suffix)
    assert body["title"] == title
    assert isinstance(body["detail"], str) and body["detail"]
    return body


def test_a_request_that_fails_validation_is_a_problem_listing_each_error(client):
    invalid = {"messages": [{"author": "operator", "body": "", "sent_at": NOW.isoformat()}]}

    body = assert_problem(client.post("/tickets", json=invalid), 422, ":validation-error", "Request validation failed")

    assert set(body) == {"type", "title", "status", "detail", "errors"}
    assert [(error["location"], error["pointer"]) for error in body["errors"]] == [
        ("body", "/messages/0/author"), ("body", "/messages/0/body"),
    ]
    assert all(error["detail"] for error in body["errors"])
    path = assert_problem(client.get("/conversations/not-a-uuid"), 422, ":validation-error", "Request validation failed")
    assert [(error["location"], error["pointer"]) for error in path["errors"]] == [("path", "/conversation_id")]


def test_a_refusal_by_the_service_is_a_problem_with_its_own_type(client):
    body = assert_problem(client.get(f"/conversations/{MISSING}"), 404, ":not-found", "Not found")

    assert body["detail"] == "conversation not found"
    assert set(body) == {"type", "title", "status", "detail"}
    approval = client.post(f"/actions/{MISSING}/approval", json={"verdict": "approve", "approver": "dana"})
    assert assert_problem(approval, 404, ":not-found", "Not found")["detail"] == "action not found"


def test_an_error_raised_by_the_framework_is_a_problem_too(client):
    unknown = assert_problem(client.get("/no-such-path"), 404, "about:blank", "Not Found")
    wrong_method = assert_problem(client.delete("/tickets"), 405, "about:blank", "Method Not Allowed")

    assert set(unknown) == set(wrong_method) == {"type", "title", "status", "detail"}


def test_an_unavailable_model_on_an_operator_turn_is_a_503_problem_to_retry(client, llm):
    llm.script(answer(DRAFT), LLMUnavailable("APITimeoutError"), LLMUnavailable("APITimeoutError"))
    conversation_id = client.post("/tickets", json=request_body("t1", NOW)).json()["conversation_id"]

    refused = client.post(f"/conversations/{conversation_id}/messages", json={"body": "Which charges were captured?"})

    body = assert_problem(refused, 503, ":model-unavailable", "Model unavailable")
    assert int(refused.headers["Retry-After"]) > 0
    assert "APITimeoutError" in body["detail"]
    assert body["conversation_id"] == conversation_id


def test_a_misconfigured_model_at_ingest_stores_the_ticket_and_answers_503_without_a_fallback(client, llm):
    ticket = {**request_body("t1", NOW), "ticket_id": "T-1"}
    llm.script(LLMMisconfigured(REJECTED), answer(DRAFT))

    refused = client.post("/tickets", json=ticket)

    body = assert_problem(refused, 503, ":model-misconfigured", "Model misconfigured")
    assert REJECTED in body["detail"]
    assert "Retry-After" not in refused.headers
    conversation = client.get(f"/conversations/{body['conversation_id']}").json()
    assert len(conversation["messages"]) == 4
    assert conversation["decisions"] == []
    assert [(turn["kind"], turn["status"]) for turn in conversation["turns"]] == [("ingest", "failed")]
    assert [step["ok"] for step in conversation["turns"][0]["steps"]] == [False]

    fixed = client.post("/tickets", json=ticket)

    assert fixed.status_code == 200
    assert (fixed.json()["decision"]["version"], fixed.json()["decision"]["source"]) == (1, "model")


def test_a_misconfigured_model_on_an_operator_turn_answers_the_same_503(client, llm):
    llm.script(answer(DRAFT), LLMMisconfigured(REJECTED))
    conversation_id = client.post("/tickets", json=request_body("t1", NOW)).json()["conversation_id"]

    refused = client.post(f"/conversations/{conversation_id}/messages", json={"body": "Which charges were captured?"})

    body = assert_problem(refused, 503, ":model-misconfigured", "Model misconfigured")
    assert REJECTED in body["detail"]
    assert body["conversation_id"] == conversation_id
    conversation = client.get(f"/conversations/{conversation_id}").json()
    assert [decision["version"] for decision in conversation["decisions"]] == [1]
    assert conversation["turns"][-1]["status"] == "failed"
