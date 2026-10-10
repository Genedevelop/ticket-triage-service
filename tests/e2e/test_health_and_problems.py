from evals.sample import request_body
from tests.conftest import NOW
from tests.e2e.conftest import wait_until

PROBLEM = "application/problem+json"
MISSING = "00000000-0000-0000-0000-000000000000"


def test_the_served_application_answers_healthz_over_a_socket(unscripted_server):
    response = unscripted_server.http.get("/healthz")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["server"] == "uvicorn"
    assert response.json() == {"status": "ok", "database": True}


def test_an_invalid_request_is_answered_with_the_validation_problem_document(unscripted_server):
    invalid = {"messages": [{"author": "operator", "body": "", "sent_at": NOW.isoformat()}]}

    refused = unscripted_server.http.post("/tickets", json=invalid)

    assert refused.status_code == 422
    assert refused.headers["content-type"] == PROBLEM
    assert refused.json() == {
        "type": "urn:ticket-triage:problem:validation-error",
        "title": "Request validation failed",
        "status": 422,
        "detail": "2 part(s) of the request are missing or invalid",
        "errors": [
            {"location": "body", "pointer": "/messages/0/author", "detail": refused.json()["errors"][0]["detail"]},
            {"location": "body", "pointer": "/messages/0/body", "detail": refused.json()["errors"][1]["detail"]},
        ],
    }
    assert all(error["detail"] for error in refused.json()["errors"])


def test_a_conversation_and_a_path_that_do_not_exist_are_answered_with_404_problem_documents(unscripted_server):
    unknown_conversation = unscripted_server.http.get(f"/conversations/{MISSING}")
    unknown_action = unscripted_server.http.post(f"/actions/{MISSING}/approval", json={"verdict": "approve", "approver": "dana"})
    unknown_path = unscripted_server.http.get("/no-such-path")

    assert [response.status_code for response in (unknown_conversation, unknown_action, unknown_path)] == [404, 404, 404]
    assert {response.headers["content-type"] for response in (unknown_conversation, unknown_action, unknown_path)} == {PROBLEM}
    assert unknown_conversation.json() == {
        "type": "urn:ticket-triage:problem:not-found", "title": "Not found", "status": 404, "detail": "conversation not found",
    }
    assert unknown_action.json()["detail"] == "action not found"
    assert unknown_path.json() == {"type": "about:blank", "title": "Not Found", "status": 404, "detail": "Not Found"}


def test_an_unhandled_failure_is_answered_with_the_servers_plain_text_500_and_the_server_keeps_serving(unscripted_server):
    failed = unscripted_server.http.post("/tickets", json=request_body("t3", NOW))

    assert failed.status_code == 500
    assert failed.headers["content-type"].startswith("text/plain")
    assert failed.text == "Internal Server Error"
    wait_until(lambda: "ScriptExhausted" in unscripted_server.log(), "the failure to be written to the server's log")
    assert unscripted_server.http.get("/healthz").status_code == 200
