from uuid import uuid4

from backend.application.tools.registry import REGISTRY, citable_tool_names, read_tool_names, run_tool
from backend.application.tools.tool import ToolContext, ToolError
from backend.domain.ports import ProviderError, ToolCall
from tests.conftest import NOW

# a context for calls that are answered before any provider or the database is reached
CONTEXT = ToolContext(conversation_id=uuid4(), customer_id=None, turn_id=uuid4(), now=NOW, providers=None, pool=None)


def test_arguments_that_are_not_json_are_answered_with_an_error_worth_another_try_and_kept_as_sent():
    raw, result, is_ok = run_tool(ToolCall(call_id="call_1", name="billing_lookup", arguments='{"charge_id": '), CONTEXT)

    assert raw == {"raw": '{"charge_id": '}
    assert result == {"error": {"type": "invalid_arguments", "message": "the arguments are not valid JSON", "retryable": True}}
    assert is_ok is False


def test_a_tool_that_is_not_registered_is_answered_with_an_error_naming_it():
    raw, result, is_ok = run_tool(ToolCall(call_id="call_1", name="delete_account", arguments='{"id": 1}'), CONTEXT)

    assert raw == {"id": 1}
    assert result == {"error": {"type": "unknown_tool", "message": "there is no tool named delete_account", "retryable": False}}
    assert is_ok is False


def test_arguments_outside_the_tools_schema_are_answered_with_an_error_naming_the_field():
    raw, result, is_ok = run_tool(ToolCall(call_id="call_1", name="propose_refund", arguments='{"charge_id": "ch_1"}'), CONTEXT)

    assert raw == {"charge_id": "ch_1"}
    assert (result["error"]["type"], result["error"]["retryable"]) == ("invalid_arguments", True)
    assert result["error"]["message"].startswith("reason: ")
    assert is_ok is False


def test_empty_arguments_are_read_as_no_arguments_and_a_ticket_without_a_customer_gets_a_tool_error():
    raw, result, is_ok = run_tool(ToolCall(call_id="call_1", name="billing_lookup", arguments=""), CONTEXT)

    assert raw == {}
    assert (result["error"]["type"], result["error"]["retryable"]) == ("customer_unknown", False)
    assert is_ok is False


def test_a_provider_error_becomes_a_tool_error_with_the_same_type_message_and_retry_advice():
    error = ToolError.from_provider(ProviderError("timeout", "the provider did not answer in time", is_retryable=True))

    assert error.as_result() == {"error": {"type": "timeout", "message": "the provider did not answer in time", "retryable": True}}


def test_the_read_tools_are_the_ones_without_an_effect_and_only_the_search_returns_citable_articles():
    assert read_tool_names() == {"billing_lookup", "status_check", "kb_search"}
    assert set(REGISTRY) - read_tool_names() == {"propose_refund", "open_incident"}
    assert citable_tool_names() == {"kb_search"}
