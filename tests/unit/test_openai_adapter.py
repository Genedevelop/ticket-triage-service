from types import SimpleNamespace

import httpx
import openai
import pytest

from backend.domain.ports import LLMMisconfigured, LLMReply, LLMUnavailable, ToolCall, ToolSpec
from backend.infrastructure.llm import openai_responses
from backend.infrastructure.llm.openai_responses import OpenAIResponsesLLM

KEY = "test-key-not-real"
REQUEST = httpx.Request("POST", "https://vendor.invalid/v1/responses")
TOOL = ToolSpec(name="billing_lookup", description="Read the billing account.", parameters={"type": "object", "properties": {}})
SCHEMA = {"type": "object", "properties": {"urgency": {"type": "string"}}}
MESSAGE = SimpleNamespace(type="message", role="assistant")
REASONING = SimpleNamespace(type="reasoning", encrypted_content="opaque")
FUNCTION_CALL = SimpleNamespace(type="function_call", call_id="call_7", name="billing_lookup", arguments="{}")


def vendor_response(output, text, status="completed", usage=SimpleNamespace(input_tokens=120, output_tokens=30)):
    return SimpleNamespace(output=output, output_text=text, status=status, model="gpt-4.1-mini-2025-04-14", usage=usage)


def status_error(error_class, status, body):
    return error_class("Incorrect API key provided: vendor-secret-text", response=httpx.Response(status, request=REQUEST), body=body)


# the vendor's client replaced by one that records how it was built and what it was sent, and answers from a list
@pytest.fixture()
def vendor(monkeypatch):
    recorded = SimpleNamespace(built=[], requests=[], answers=[])

    def create(**request):
        recorded.requests.append(request)
        answered = recorded.answers.pop(0)
        if isinstance(answered, Exception):
            raise answered
        return answered

    def build(**parameters):
        recorded.built.append(parameters)
        return SimpleNamespace(responses=SimpleNamespace(create=create))

    monkeypatch.setattr(openai_responses.openai, "OpenAI", build)
    return recorded


def respond(llm, tools=(TOOL,), timeout_s=30.0):
    return llm.respond(
        instructions="Triage the ticket.", items=[{"role": "user", "content": "hello"}], tools=list(tools),
        output_schema=SCHEMA, timeout_s=timeout_s,
    )


def test_a_missing_key_is_reported_as_misconfigured_without_calling_the_vendor(vendor):
    llm = OpenAIResponsesLLM(api_key="", model="gpt-4.1-mini")

    with pytest.raises(LLMMisconfigured, match="OPENAI_API_KEY"):
        llm.respond(instructions="x", items=[], tools=[], output_schema={}, timeout_s=1)

    assert vendor.built == []
    assert vendor.requests == []


def test_the_adapter_builds_the_input_items_in_the_shape_of_the_responses_api():
    llm = OpenAIResponsesLLM(api_key="", model="gpt-4.1-mini")

    assert llm.user_message("héllo") == {"role": "user", "content": "héllo"}
    assert llm.tool_result("call_1", {"plan": "prö"}) == {
        "type": "function_call_output", "call_id": "call_1", "output": '{"plan": "prö"}',
    }


def test_the_vendors_client_is_built_once_with_the_key_and_without_its_own_retries(vendor):
    vendor.answers += [vendor_response([MESSAGE], "{}"), vendor_response([MESSAGE], "{}")]
    llm = OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini")

    respond(llm)
    respond(llm)

    assert vendor.built == [{"api_key": KEY, "max_retries": 0}]
    assert len(vendor.requests) == 2


def test_the_request_is_stateless_and_strict_and_carries_the_tools_the_schema_and_the_time_out(vendor):
    vendor.answers.append(vendor_response([MESSAGE], "{}"))

    respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"), timeout_s=12.5)

    assert vendor.requests == [{
        "model": "gpt-4.1-mini",
        "instructions": "Triage the ticket.",
        "input": [{"role": "user", "content": "hello"}],
        "tools": [{
            "type": "function", "name": "billing_lookup", "description": "Read the billing account.", "strict": True,
            "parameters": {"type": "object", "properties": {}},
        }],
        "text": {"format": {"type": "json_schema", "name": "decision", "strict": True, "schema": SCHEMA}},
        "store": False,
        "parallel_tool_calls": False,
        "timeout": 12.5,
    }]


def test_reasoning_is_requested_with_its_encrypted_content_only_when_an_effort_is_configured(vendor):
    vendor.answers.append(vendor_response([MESSAGE], "{}"))

    respond(OpenAIResponsesLLM(api_key=KEY, model="o4-mini", reasoning_effort="low"))

    (request,) = vendor.requests
    assert request["reasoning"] == {"effort": "low"}
    assert request["include"] == ["reasoning.encrypted_content"]


def test_a_final_answer_is_returned_with_its_text_the_model_the_vendor_reported_and_the_token_counts(vendor):
    vendor.answers.append(vendor_response([REASONING, MESSAGE], '{"urgency": "high"}'))

    reply = respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"))

    assert reply == LLMReply(
        tool_calls=(), text='{"urgency": "high"}', carry=(REASONING, MESSAGE), model="gpt-4.1-mini-2025-04-14",
        input_tokens=120, output_tokens=30,
    )


def test_function_calls_are_returned_as_tool_calls_with_every_output_item_carried_forward_and_no_text(vendor):
    vendor.answers.append(vendor_response([REASONING, FUNCTION_CALL], "", status="incomplete"))

    reply = respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"))

    assert reply.tool_calls == (ToolCall(call_id="call_7", name="billing_lookup", arguments="{}"),)
    assert reply.text is None
    assert reply.carry == (REASONING, FUNCTION_CALL)


def test_a_reply_without_usage_reports_no_token_counts(vendor):
    vendor.answers.append(vendor_response([MESSAGE], "{}", usage=None))

    reply = respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"))

    assert (reply.input_tokens, reply.output_tokens) == (None, None)


@pytest.mark.parametrize(
    ("status", "text"),
    [("incomplete", '{"urgency": "hi'), ("completed", ""), ("completed", None), ("failed", None)],
)
def test_an_answer_that_is_truncated_or_empty_is_reported_as_unavailable_with_its_status(vendor, status, text):
    vendor.answers.append(vendor_response([MESSAGE], text, status=status))

    with pytest.raises(LLMUnavailable) as refused:
        respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"))

    assert refused.value.message == f"model returned no usable answer (status: {status})"


@pytest.mark.parametrize(
    ("vendor_error", "described"),
    [
        (status_error(openai.AuthenticationError, 401, {"code": "invalid_api_key"}), "AuthenticationError 401 invalid_api_key"),
        (status_error(openai.PermissionDeniedError, 403, None), "PermissionDeniedError 403"),
        (status_error(openai.NotFoundError, 404, {"code": "model_not_found"}), "NotFoundError 404 model_not_found"),
        (
            status_error(openai.RateLimitError, 429, {"type": "insufficient_quota", "code": "insufficient_quota"}),
            "RateLimitError 429 insufficient_quota",
        ),
    ],
)
def test_an_error_only_a_person_can_fix_is_reported_as_misconfigured(vendor, vendor_error, described):
    vendor.answers.append(vendor_error)

    with pytest.raises(LLMMisconfigured) as refused:
        respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"))

    assert refused.value.message == described
    assert refused.value.__cause__ is vendor_error


@pytest.mark.parametrize(
    ("vendor_error", "described"),
    [
        (
            status_error(openai.RateLimitError, 429, {"type": "requests", "code": "rate_limit_exceeded"}),
            "RateLimitError 429 rate_limit_exceeded",
        ),
        (status_error(openai.RateLimitError, 429, None), "RateLimitError 429"),
        (status_error(openai.InternalServerError, 500, None), "InternalServerError 500"),
        (status_error(openai.BadRequestError, 400, {"code": "invalid_json_schema"}), "BadRequestError 400 invalid_json_schema"),
        (openai.APITimeoutError(request=REQUEST), "APITimeoutError"),
        (openai.APIConnectionError(request=REQUEST), "APIConnectionError"),
    ],
)
def test_a_rate_limit_a_time_out_and_every_other_vendor_error_is_reported_as_unavailable(vendor, vendor_error, described):
    vendor.answers.append(vendor_error)

    with pytest.raises(LLMUnavailable) as refused:
        respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"))

    assert refused.value.message == described
    assert refused.value.__cause__ is vendor_error


def test_the_reported_error_repeats_neither_the_vendors_message_nor_the_key(vendor):
    vendor.answers.append(status_error(openai.AuthenticationError, 401, {"code": "invalid_api_key"}))

    with pytest.raises(LLMMisconfigured) as refused:
        respond(OpenAIResponsesLLM(api_key=KEY, model="gpt-4.1-mini"))

    assert "vendor-secret-text" not in refused.value.message
    assert KEY not in refused.value.message
    assert refused.value.details == {}
