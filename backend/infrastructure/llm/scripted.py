import json
from typing import Any

from backend.core.errors import Internal
from backend.domain.ports import LLMReply, ToolCall, ToolSpec
from backend.infrastructure.llm.openai_responses import tool_result_item, user_item

MODEL_ID = "scripted-model"


class ScriptExhausted(Internal):
    code = "script-exhausted"
    title = "Script exhausted"


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"tool": name, "arguments": arguments}


def answer(draft: dict[str, Any] | str) -> dict[str, Any]:
    return {"text": draft if isinstance(draft, str) else json.dumps(draft, ensure_ascii=False)}


class ScriptedLLM:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._steps: list[Any] = []
        self._repeated_step: Any = None

    def script(self, *steps: Any, then_forever: Any = None) -> None:
        self._steps = list(steps)
        self._repeated_step = then_forever

    def respond(
        self,
        *,
        instructions: str,
        items: list[Any],
        tools: list[ToolSpec],
        output_schema: dict[str, Any],
        timeout_s: float,
    ) -> LLMReply:
        # Step 1: Keep the request, so a test can read what the model was sent
        self.requests.append({
            "instructions": instructions,
            "items": list(items),
            "tools": list(tools),
            "output_schema": output_schema,
            "timeout_s": timeout_s,
        })
        # Step 2: Take the next scripted step, or the repeated one once the listed steps are used up
        if self._steps:
            step = self._steps.pop(0)
        elif self._repeated_step is not None:
            step = self._repeated_step
        else:
            raise ScriptExhausted("the scripted model was called more often than its script allows")
        # Step 3: Raise a scripted exception instead of answering
        if isinstance(step, Exception):
            raise step
        # Step 4: Answer a scripted tool call with the call and the item the vendor would carry forward
        if "tool" in step:
            arguments = json.dumps(step["arguments"])
            call_id = f"call_{len(self.requests)}"
            calls = (ToolCall(call_id=call_id, name=step["tool"], arguments=arguments),)
            carry = ({"type": "function_call", "call_id": call_id, "name": step["tool"], "arguments": arguments},)
            return LLMReply(tool_calls=calls, text=None, carry=carry, model=MODEL_ID, input_tokens=0, output_tokens=0)
        # Step 5: Answer a scripted final answer with its text
        carry = ({"role": "assistant", "content": step["text"]},)
        return LLMReply(tool_calls=(), text=step["text"], carry=carry, model=MODEL_ID, input_tokens=0, output_tokens=0)

    def user_message(self, text: str) -> dict[str, Any]:
        return user_item(text)

    def tool_result(self, call_id: str, result: Any) -> dict[str, Any]:
        return tool_result_item(call_id, result)
