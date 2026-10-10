from backend.domain.ports import LLMUnavailable
from backend.infrastructure.llm.scripted import answer, call
from evals.sample import request_body
from tests.conftest import NOW
from tests.unit.test_rules import DRAFT


def post_ticket(client):
    return client.post("/tickets", json=request_body("t1", NOW))


def assert_fallback(posted, reason):
    decision = posted.json()["decision"]
    assert posted.status_code == 201
    assert decision["source"] == "fallback"
    assert decision["fallback_reason"] == reason
    assert decision["next_action"] == "escalate_to_human"
    assert decision["urgency"] is None
    return decision


def test_invalid_output_twice_falls_back_to_a_person(client, llm):
    llm.script(answer({**DRAFT, "urgency": "urgent"}), answer("not json"))

    posted = post_ticket(client)

    assert_fallback(posted, "invalid_output")
    assert len(llm.requests) == 2
    assert "urgency" in llm.requests[1]["items"][-1]["content"]
    conversation = client.get(f"/conversations/{posted.json()['conversation_id']}").json()
    assert conversation["decisions"][0]["source"] == "fallback"
    assert conversation["turns"][0]["status"] == "completed"


def test_invalid_output_once_is_corrected(client, llm):
    llm.script(answer({**DRAFT, "next_action": "refund_now"}), answer(DRAFT))

    decision = post_ticket(client).json()["decision"]

    assert (decision["source"], decision["next_action"]) == ("model", "escalate_to_human")


def test_a_model_that_keeps_calling_tools_is_stopped(client, llm):
    llm.script(then_forever=call("billing_lookup"))

    decision = assert_fallback(post_ticket(client), "step_limit")

    assert len(llm.requests) == 8
    assert len(decision["tools_called"]) == 8


def test_a_model_past_the_turn_deadline_is_stopped(client, llm, monkeypatch):
    llm.script(then_forever=call("billing_lookup"))
    ticks = iter(range(0, 10_000, 25))
    monkeypatch.setattr("backend.application.agent_loop.monotonic", lambda: next(ticks))

    assert_fallback(post_ticket(client), "deadline_exceeded")

    assert len(llm.requests) < 8


def test_a_failed_model_call_is_tried_once_more(client, llm):
    llm.script(LLMUnavailable("APITimeoutError"), answer(DRAFT))

    decision = post_ticket(client).json()["decision"]

    assert decision["source"] == "model"


def test_a_model_that_stays_unavailable_falls_back_with_the_vendor_code(client, llm):
    llm.script(LLMUnavailable("RateLimitError 429 rate_limit_exceeded"), LLMUnavailable("RateLimitError 429 rate_limit_exceeded"))

    assert_fallback(post_ticket(client), "model_unavailable: RateLimitError 429 rate_limit_exceeded")


def test_a_thread_too_large_for_the_model_goes_to_a_person_without_a_model_call(client, llm):
    body = request_body("t1", NOW)
    body["messages"][0]["body"] = "x" * 70_000

    assert_fallback(client.post("/tickets", json=body), "input_too_large")

    assert llm.requests == []


def test_a_call_to_a_tool_that_does_not_exist_is_answered_with_an_error(client, llm):
    llm.script(call("refund_everything", charge_id="ch_t1_1"), answer(DRAFT))

    decision = post_ticket(client).json()["decision"]

    assert [(tool["name"], tool["ok"]) for tool in decision["tools_called"]] == [("refund_everything", False)]


def test_the_loop_sends_input_items_in_whatever_shape_the_model_adapter_builds(client, llm):
    llm.user_message = lambda text: ("said", text)
    llm.tool_result = lambda call_id, result: ("returned", call_id, result["source"])
    llm.script(call("status_check"), answer({**DRAFT, "urgency": "urgent"}), answer(DRAFT))

    post_ticket(client)

    first, second, third = (request["items"] for request in llm.requests)
    assert [item[0] for item in first] == ["said"]
    assert second[-1] == ("returned", "call_1", "public status page")
    assert third[-1][0] == "said" and "urgency" in third[-1][1]
