import json
from typing import Any

import openai

from backend.domain.ports import LLMMisconfigured, LLMReply, LLMUnavailable, ToolCall, ToolSpec

# the vendor will never accept the request until a person changes the key, the model id or the account
_MISCONFIGURED = (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)
# an account without credit answers 429 like a rate limit, but waiting never fixes it
_OUT_OF_CREDIT = "insufficient_quota"
OUTPUT_SCHEMA_NAME = "decision"


def user_item(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def tool_result_item(call_id: str, result: Any) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)}


class OpenAIResponsesLLM:
    def __init__(self, api_key: str, model: str, reasoning_effort: str | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client: openai.OpenAI | None = None

    def _get_client(self) -> openai.OpenAI:
        # Step 1: Report a missing key only now, so the service starts without one
        if not self._api_key:
            raise LLMMisconfigured("OPENAI_API_KEY is not set")
        # Step 2: Build the client once, without the SDK's retries, because the turn loop owns retries and deadlines
        if self._client is None:
            self._client = openai.OpenAI(api_key=self._api_key, max_retries=0)
        return self._client

    def _build_request(
        self, instructions: str, items: list[Any], tools: list[ToolSpec], output_schema: dict[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        # Step 1: Describe a stateless call: nothing is stored at the vendor, and every step resends its input
        request: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": items,
            "tools": [
                {"type": "function", "name": tool.name, "description": tool.description, "strict": True, "parameters": tool.parameters}
                for tool in tools
            ],
            "text": {"format": {"type": "json_schema", "name": OUTPUT_SCHEMA_NAME, "strict": True, "schema": output_schema}},
            "store": False,
            "parallel_tool_calls": False,
            "timeout": timeout_s,
        }
        # Step 2: Ask for reasoning only when configured, because a non-reasoning model rejects the parameter
        if self._reasoning_effort:
            request["reasoning"] = {"effort": self._reasoning_effort}
            request["include"] = ["reasoning.encrypted_content"]
        return request

    def _send(self, request: dict[str, Any]) -> Any:
        # Step 1: Take the client, which reports a missing key before anything is sent
        client = self._get_client()
        # Step 2: Call the vendor, reporting what a person must fix as misconfigured and the rest as unavailable
        try:
            return client.responses.create(**request)
        except _MISCONFIGURED as error:
            raise LLMMisconfigured(_describe(error)) from error
        except openai.RateLimitError as error:
            if _error_type(error) == _OUT_OF_CREDIT:
                raise LLMMisconfigured(_describe(error)) from error
            raise LLMUnavailable(_describe(error)) from error
        except openai.OpenAIError as error:
            raise LLMUnavailable(_describe(error)) from error

    def respond(
        self,
        *,
        instructions: str,
        items: list[Any],
        tools: list[ToolSpec],
        output_schema: dict[str, Any],
        timeout_s: float,
    ) -> LLMReply:
        # Step 1: Send the request, turning every vendor error into one of the two errors the port names
        response = self._send(self._build_request(instructions, items, tools, output_schema, timeout_s))
        # Step 2: Read the tool calls, or the final text when there are none
        calls = tuple(
            ToolCall(call_id=item.call_id, name=item.name, arguments=item.arguments)
            for item in response.output
            if item.type == "function_call"
        )
        text = None if calls else (response.output_text or None)
        # Step 3: Refuse a refusal or a truncated answer, which carries no decision the loop can use
        if not calls and (response.status != "completed" or text is None):
            raise LLMUnavailable(f"model returned no usable answer (status: {response.status})")
        # Step 4: Give the reply with the items to carry forward and the token counts
        usage = response.usage
        return LLMReply(
            tool_calls=calls,
            text=text,
            carry=tuple(response.output),
            model=response.model,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
        )

    def user_message(self, text: str) -> dict[str, Any]:
        return user_item(text)

    def tool_result(self, call_id: str, result: Any) -> dict[str, Any]:
        return tool_result_item(call_id, result)


def _error_type(error: openai.APIError) -> str | None:
    body = getattr(error, "body", None)
    return body.get("type") if isinstance(body, dict) else getattr(error, "type", None)


def _describe(error: Exception) -> str:
    # Step 1: Start from the class of the error, which never carries the key
    parts = [type(error).__name__]
    # Step 2: Add the HTTP status and the vendor's code when the error has them
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    if status:
        parts.append(str(status))
    if code:
        parts.append(str(code))
    return " ".join(parts)
