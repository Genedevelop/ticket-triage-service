import json
import os

from fastapi.testclient import TestClient

from backend.core.config import load_settings
from backend.infrastructure.llm.openai_responses import OpenAIResponsesLLM
from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from main import create_app
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT


def test_ticket_one_is_triaged_with_a_billing_lookup_and_read_back(client, llm):
    llm.script(call("billing_lookup"), answer(DRAFT))

    posted = client.post("/tickets", json=request_body("t1", NOW))

    assert posted.status_code == 201
    decision = posted.json()["decision"]
    assert {key: decision[key] for key in DRAFT} == DRAFT
    assert decision["version"] == 1
    assert decision["source"] == "model"
    assert decision["fallback_reason"] is None
    assert decision["model"] == "scripted-model"
    assert decision["prompt_version"].startswith("triage_v2@")
    assert [(tool["name"], tool["arguments"], tool["ok"]) for tool in decision["tools_called"]] == [
        ("billing_lookup", {}, True)
    ]

    read = client.get(f"/conversations/{posted.json()['conversation_id']}")

    assert read.status_code == 200
    conversation = read.json()
    assert [message["author"] for message in conversation["messages"]] == ["customer"] * 4
    assert conversation["messages"][0]["body"].startswith("My payment failed")
    assert conversation["decisions"] == [decision]
    (turn,) = conversation["turns"]
    assert (turn["kind"], turn["status"]) == ("ingest", "completed")
    assert [(step["kind"], step["name"]) for step in turn["steps"]] == [
        ("model", "scripted-model"),
        ("tool", "billing_lookup"),
        ("model", "scripted-model"),
    ]
    lookup = turn["steps"][1]["result"]
    assert lookup["plan"] == "free"
    assert lookup["last_upgrade_attempt"]["status"] == "failed"
    assert sorted(attempt["state"] for attempt in lookup["payment_attempts"]) == ["authorized", "captured", "captured"]
    assert {attempt["amount"] for attempt in lookup["payment_attempts"]} == {"29.99"}


def test_the_model_reads_the_thread_as_data_and_the_tool_result_on_the_next_step(client, llm):
    llm.script(call("billing_lookup"), answer(DRAFT))

    client.post("/tickets", json=request_body("t1", NOW))

    first, second = llm.requests
    assert "<!--" not in first["instructions"]
    assert [tool.name for tool in first["tools"]] == ["billing_lookup", "status_check", "kb_search", "propose_refund", "open_incident"]
    assert "I have THREE charges of $29.99 now" in first["items"][0]["content"]
    assert '"age_minutes": 180' in first["items"][0]["content"]
    output = json.loads(second["items"][-1]["output"])
    assert second["items"][-1]["type"] == "function_call_output"
    assert output["plan"] == "free"


def test_a_ticket_id_seen_before_continues_its_conversation(client, llm):
    body = request_body("t1", NOW)
    for number, message in enumerate(body["messages"], start=1):
        message["id"] = f"m{number}"
    llm.script(answer(DRAFT), answer({**DRAFT, "urgency": "critical"}))

    first = client.post("/tickets", json={**body, "ticket_id": "T-1", "messages": body["messages"][:3]})
    second = client.post("/tickets", json={**body, "ticket_id": "T-1"})

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["conversation_id"] == first.json()["conversation_id"]
    assert second.json()["decision"]["version"] == 2
    conversation = client.get(f"/conversations/{first.json()['conversation_id']}").json()
    assert [message["id"] for message in conversation["messages"]] == ["m1", "m2", "m3", "m4"]
    assert [decision["urgency"] for decision in conversation["decisions"]] == ["high", "critical"]
    assert '"version": 1' in llm.requests[1]["items"][0]["content"]


def test_a_ticket_without_a_customer_id_is_still_triaged_and_the_lookup_fails_visibly(client, llm):
    body = request_body("t1", NOW)
    del body["customer"]
    llm.script(call("billing_lookup"), answer(DRAFT))

    posted = client.post("/tickets", json=body)

    assert posted.status_code == 201
    (tool,) = posted.json()["decision"]["tools_called"]
    assert (tool["name"], tool["ok"]) == ("billing_lookup", False)
    error = json.loads(llm.requests[1]["items"][-1]["output"])["error"]
    assert set(error) == {"type", "message", "retryable"}


def test_an_unknown_conversation_is_not_found(client):
    assert client.get("/conversations/00000000-0000-0000-0000-000000000000").status_code == 404


def test_a_ticket_without_messages_is_rejected(client):
    assert client.post("/tickets", json={"messages": []}).status_code == 422


def test_without_a_model_passed_in_the_service_starts_on_the_real_adapter_and_needs_no_key(empty_database):
    settings = load_settings({**os.environ, "DATABASE_URL": empty_database, "OPENAI_API_KEY": ""})
    app = create_app(settings=settings)

    with TestClient(app) as served:
        assert served.get("/healthz").status_code == 200
        assert isinstance(app.state.llm, OpenAIResponsesLLM)
        assert app.state.providers.billing.lookup("cust_t1", NOW)["plan"] == "free"
