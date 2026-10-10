from backend.core import logging as log
from backend.domain.ports import LLMUnavailable
from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT
from tests.unit.test_log_events import events

IDS = ("request_id", "conversation_id", "turn_id", "action_id", "duration_ms", "outcome")
SECRET = "duplicate charge on the card ending 4242"


def named(lines, name):
    return [line for line in lines if line["event"] == name]


def test_a_triaged_ticket_logs_one_json_line_per_event_with_its_ids(client, llm, caplog):
    llm.script(call("billing_lookup"), call("propose_refund", charge_id="ch_t1_1", reason=SECRET), answer(DRAFT))

    posted = client.post("/tickets", json=request_body("t1", NOW), headers={"X-Request-Id": "req-1"})

    conversation = client.get(f"/conversations/{posted.json()['conversation_id']}").json()
    lines = events(caplog)
    assert [line["event"] for line in lines] == [
        "turn.started", "llm.call", "tool.call", "llm.call", "action.transition", "tool.call", "llm.call", "decision.made",
    ]
    for line in lines:
        assert set(IDS) <= set(line)
        assert line["request_id"] == "req-1"
        assert line["conversation_id"] == conversation["id"]
        assert line["turn_id"] == conversation["turns"][0]["id"]
    assert posted.headers["X-Request-Id"] == "req-1"
    assert [line["outcome"] for line in named(lines, "llm.call")] == ["ok", "ok", "ok"]
    assert [(line["tool"], line["outcome"]) for line in named(lines, "tool.call")] == [
        ("billing_lookup", "ok"), ("propose_refund", "ok"),
    ]
    assert all(isinstance(line["duration_ms"], int) for line in lines if line["event"] in ("llm.call", "tool.call"))
    transition = named(lines, "action.transition")[0]
    assert transition["action_id"] == posted.json()["decision"]["actions"][0]["id"]
    assert (transition["transition"], transition["outcome"]) == ("proposed", "pending_approval")
    made = named(lines, "decision.made")[0]
    assert (made["outcome"], made["urgency"], made["version"]) == ("escalate_to_human", "high", 1)


def test_message_bodies_and_tool_payloads_are_not_logged_at_the_default_level(client, llm, caplog):
    llm.script(call("billing_lookup"), call("propose_refund", charge_id="ch_t1_1", reason=SECRET), answer(DRAFT))

    client.post("/tickets", json=request_body("t1", NOW))

    logged = "\n".join(record.getMessage() for record in caplog.records if record.name == log.LOGGER)
    assert logged
    assert SECRET not in logged
    assert "presentation in 2 hours" not in logged
    assert DRAFT["rationale"] not in logged
    assert "payload" not in logged


def test_a_turn_the_model_could_not_finish_logs_the_fallback(client, llm, caplog):
    llm.script(LLMUnavailable("timeout"), LLMUnavailable("timeout"))

    client.post("/tickets", json=request_body("t1", NOW))

    lines = events(caplog)
    assert [line["event"] for line in lines] == ["turn.started", "llm.call", "llm.call", "turn.fallback", "decision.made"]
    assert [line["outcome"] for line in named(lines, "llm.call")] == ["unavailable", "unavailable"]
    assert named(lines, "turn.fallback")[0]["outcome"] == "model_unavailable: timeout"
    assert named(lines, "decision.made")[0]["source"] == "fallback"


def test_an_approval_logs_each_transition_of_its_action_under_the_request(client, llm, caplog):
    llm.script(call("propose_refund", charge_id="ch_t1_1", reason=SECRET), answer(DRAFT))
    action_id = client.post("/tickets", json=request_body("t1", NOW)).json()["decision"]["actions"][0]["id"]
    caplog.clear()

    approved = client.post(f"/actions/{action_id}/approval", json={"verdict": "approve", "approver": "dana"})

    lines = events(caplog)
    assert [(line["event"], line["transition"], line["outcome"]) for line in lines] == [
        ("action.transition", "approved", "approved"),
        ("action.transition", "claimed", "executing"),
        ("action.transition", "provider_result", "succeeded"),
    ]
    assert {line["action_id"] for line in lines} == {action_id}
    assert {line["request_id"] for line in lines} == {approved.headers["X-Request-Id"]}


def test_a_request_without_an_id_is_given_one(client):
    first, second = client.get("/healthz"), client.get("/healthz")

    assert first.headers["X-Request-Id"]
    assert first.headers["X-Request-Id"] != second.headers["X-Request-Id"]
