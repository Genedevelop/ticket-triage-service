import json
import sys
import time

from backend.core.config import load_settings
from backend.domain.ports import LLMMisconfigured, LLMUnavailable, ToolSpec
from backend.infrastructure.llm.openai_responses import OpenAIResponsesLLM

INSTRUCTIONS = "Triage the ticket."
MODEL_TIMEOUT_S = 30
ELAPSED_DECIMALS = 2
TOOL = ToolSpec(
    name="billing_lookup",
    description="Read this customer's plan and payment attempts from the billing system.",
    parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)
SCHEMA = {
    "type": "object",
    "properties": {
        "urgency": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "rationale": {"type": "string"},
    },
    "required": ["urgency", "rationale"],
    "additionalProperties": False,
}


def main() -> int:
    # Step 1: Build the real adapter from the environment, the first input and the clock
    settings = load_settings()
    llm = OpenAIResponsesLLM(settings.llm_api_key, settings.model, settings.reasoning_effort)
    items = [llm.user_message("Ticket: I was charged three times for one upgrade. Call billing_lookup, then decide.")]
    started = time.monotonic()
    try:
        # Step 2: Ask for a strict tool call and answer it with a canned result
        first = llm.respond(instructions=INSTRUCTIONS, items=items, tools=[TOOL], output_schema=SCHEMA, timeout_s=MODEL_TIMEOUT_S)
        for call in first.tool_calls:
            items += [*first.carry, llm.tool_result(call.call_id, {"charges": 3, "state": "captured"})]
        # Step 3: Ask for the strict final answer, unless the model gave it at once
        final = first if not first.tool_calls else llm.respond(
            instructions=INSTRUCTIONS, items=items, tools=[TOOL], output_schema=SCHEMA, timeout_s=MODEL_TIMEOUT_S
        )
    except (LLMMisconfigured, LLMUnavailable) as error:
        print(json.dumps({"ok": False, "error": type(error).__name__, "detail": str(error)}))
        return 1
    # Step 4: Print the round trip as one JSON line and exit non-zero when no answer came back
    print(json.dumps({
        "ok": final.text is not None,
        "model": final.model,
        "tool_called": [call.name for call in first.tool_calls],
        "decision": json.loads(final.text) if final.text else None,
        "seconds": round(time.monotonic() - started, ELAPSED_DECIMALS),
    }, ensure_ascii=False))
    return 0 if final.text else 1


if __name__ == "__main__":
    sys.exit(main())
