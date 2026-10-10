import json
from typing import Any

from pydantic import ValidationError

from backend.application.tools.billing_lookup import BILLING_LOOKUP
from backend.application.tools.kb_search import KB_SEARCH
from backend.application.tools.open_incident import OPEN_INCIDENT
from backend.application.tools.propose_refund import PROPOSE_REFUND
from backend.application.tools.status_check import STATUS_CHECK
from backend.application.tools.tool import Tool, ToolContext, ToolError, describe_validation_errors
from backend.domain.ports import ToolCall

REGISTRY: dict[str, Tool] = {
    tool.name: tool for tool in (BILLING_LOOKUP, STATUS_CHECK, KB_SEARCH, PROPOSE_REFUND, OPEN_INCIDENT)
}


def read_tool_names() -> frozenset[str]:
    return frozenset(name for name, tool in REGISTRY.items() if not tool.is_effect)


def citable_tool_names() -> frozenset[str]:
    return frozenset(name for name, tool in REGISTRY.items() if tool.has_citable_articles)


def run_tool(call: ToolCall, context: ToolContext) -> tuple[Any, dict[str, Any], bool]:
    # Step 1: Parse the arguments, which arrive as the JSON text the model produced
    try:
        raw = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        error = ToolError("invalid_arguments", "the arguments are not valid JSON", is_retryable=True)
        return {"raw": call.arguments}, error.as_result(), False
    # Step 2: Find the tool, answering a name that is not registered with an error
    tool = REGISTRY.get(call.name)
    if tool is None:
        return raw, ToolError("unknown_tool", f"there is no tool named {call.name}").as_result(), False
    # Step 3: Validate the arguments against the tool's schema
    try:
        arguments = tool.arguments.model_validate(raw)
    except ValidationError as error:
        return raw, ToolError("invalid_arguments", describe_validation_errors(error), is_retryable=True).as_result(), False
    # Step 4: Run the handler, turning a tool error into the result the model reads
    try:
        return raw, tool.handler(arguments, context), True
    except ToolError as error:
        return raw, error.as_result(), False
