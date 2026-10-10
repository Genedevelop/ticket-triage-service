import pytest

from backend.core.errors import Internal
from backend.domain.ports import LLMUnavailable, ToolCall
from backend.infrastructure.llm.scripted import MODEL_ID, ScriptedLLM, ScriptExhausted, answer, call


def respond(llm):
    return llm.respond(instructions="x", items=[{"role": "user", "content": "hello"}], tools=[], output_schema={}, timeout_s=1)


def test_the_scripted_model_answers_its_steps_in_order_and_keeps_every_request():
    llm = ScriptedLLM()
    llm.script(call("billing_lookup"), answer({"urgency": "high"}))

    first, second = respond(llm), respond(llm)

    assert first.tool_calls == (ToolCall(call_id="call_1", name="billing_lookup", arguments="{}"),)
    assert (first.text, first.model) == (None, MODEL_ID)
    assert first.carry == ({"type": "function_call", "call_id": "call_1", "name": "billing_lookup", "arguments": "{}"},)
    assert (second.tool_calls, second.text) == ((), '{"urgency": "high"}')
    assert second.carry == ({"role": "assistant", "content": '{"urgency": "high"}'},)
    assert [request["items"] for request in llm.requests] == [[{"role": "user", "content": "hello"}]] * 2


def test_a_call_beyond_the_script_is_refused_as_an_internal_error():
    llm = ScriptedLLM()
    llm.script(answer("only once"))
    respond(llm)

    with pytest.raises(ScriptExhausted) as refused:
        respond(llm)

    assert isinstance(refused.value, Internal)
    assert refused.value.message == "the scripted model was called more often than its script allows"
    assert len(llm.requests) == 2


def test_a_step_given_to_repeat_answers_every_call_after_the_listed_ones():
    llm = ScriptedLLM()
    llm.script(answer("first"), then_forever=call("kb_search", query="dark mode"))

    replies = [respond(llm) for _ in range(3)]

    assert replies[0].text == "first"
    assert [reply.tool_calls[0].name for reply in replies[1:]] == ["kb_search", "kb_search"]
    assert [reply.tool_calls[0].call_id for reply in replies[1:]] == ["call_2", "call_3"]


def test_a_scripted_exception_is_raised_in_place_of_an_answer():
    llm = ScriptedLLM()
    llm.script(LLMUnavailable("APITimeoutError"))

    with pytest.raises(LLMUnavailable, match="APITimeoutError"):
        respond(llm)
