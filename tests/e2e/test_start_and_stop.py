import signal

from evals.sample import request_body
from tests.conftest import NOW
from tests.e2e.conftest import REAL_FACTORY, SHUTDOWN_COMPLETE

PROBLEM = "application/problem+json"


def test_a_server_asked_to_stop_runs_its_shut_down_and_exits(serve):
    server = serve()

    exit_code = server.stop()

    assert exit_code in (0, -signal.SIGTERM)
    assert SHUTDOWN_COMPLETE in server.log()
    assert "Traceback" not in server.log()


def test_the_real_factory_without_a_key_stores_the_ticket_and_answers_503_model_misconfigured(serve):
    server = serve(factory=REAL_FACTORY)

    refused = server.http.post("/tickets", json={**request_body("t1", NOW), "ticket_id": "T-1"})

    body = refused.json()
    assert refused.status_code == 503
    assert refused.headers["content-type"] == PROBLEM
    assert "Retry-After" not in refused.headers
    assert {key: body[key] for key in ("type", "title", "status")} == {
        "type": "urn:ticket-triage:problem:model-misconfigured", "title": "Model misconfigured", "status": 503,
    }
    assert "OPENAI_API_KEY is not set" in body["detail"]
    stored = server.http.get(f"/conversations/{body['conversation_id']}")
    assert stored.status_code == 200
    assert [message["author"] for message in stored.json()["messages"]] == ["customer"] * 4
    assert stored.json()["decisions"] == []
    assert [(turn["kind"], turn["status"]) for turn in stored.json()["turns"]] == [("ingest", "failed")]
