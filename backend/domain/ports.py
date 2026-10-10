from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel

from backend.core.errors import CallFailed, Misconfigured, Unavailable


class LLMMisconfigured(Misconfigured):
    code = "llm-misconfigured"
    title = "Model misconfigured"


class LLMUnavailable(Unavailable):
    code = "llm-unavailable"
    title = "Model unavailable"


class TypedFailure(CallFailed):
    code = "typed-failure"
    title = "Typed failure"

    def __init__(self, error_type: str, message: str, is_retryable: bool = False) -> None:
        super().__init__(message, details={"error_type": error_type, "is_retryable": is_retryable})
        self.error_type = error_type
        self.is_retryable = is_retryable


class ProviderError(TypedFailure):
    code = "provider-error"
    title = "Provider error"


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    def clean(node: Any) -> Any:
        # Step 1: Clean each item of a list, and return any value that is not an object as it is
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        # Step 2: Drop titles and defaults, cleaning nested values while keeping property and definition names
        cleaned = {key: clean(value) for key, value in node.items() if key not in ("title", "default", "properties", "$defs")}
        for key in ("properties", "$defs"):
            if key in node:
                cleaned[key] = {name: clean(value) for name, value in node[key].items()}
        # Step 3: Require every property of an object and forbid extras, as the strict mode demands
        if node.get("type") == "object":
            cleaned.setdefault("properties", {})
            cleaned["required"] = list(cleaned["properties"])
            cleaned["additionalProperties"] = False
        return cleaned

    return clean(model.model_json_schema())


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMReply:
    tool_calls: tuple[ToolCall, ...]
    text: str | None
    # items to append to the input before the next step; every input item is in the vendor's shape and opaque to the loop
    carry: tuple[Any, ...]
    # the model id the vendor reported, which may be more specific than the one requested
    model: str
    input_tokens: int | None
    output_tokens: int | None


class LLM(Protocol):
    def respond(
        self,
        *,
        instructions: str,
        items: list[Any],
        tools: list[ToolSpec],
        output_schema: dict[str, Any],
        timeout_s: float,
    ) -> LLMReply:
        ...

    def user_message(self, text: str) -> Any:
        ...

    def tool_result(self, call_id: str, result: Any) -> Any:
        ...


class Billing(Protocol):
    def lookup(self, customer_id: str, now: datetime) -> dict[str, Any]:
        ...

    def get_charge(self, charge_id: str, timeout_s: float) -> dict[str, Any]:
        ...

    def refund(self, charge_id: str, *, amount_cents: int, currency: str, idempotency_key: str, timeout_s: float) -> dict[str, Any]:
        ...


class StatusPage(Protocol):
    def check(self, now: datetime) -> dict[str, Any]:
        ...


# the closed list a fault is reported under
COMPONENTS = ("web_app", "api", "login", "payments")


class Incidents(Protocol):
    def open(self, dedup_key: str, *, component: str, region: str, summary: str, timeout_s: float) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class Providers:
    billing: Billing
    status: StatusPage
    incidents: Incidents
