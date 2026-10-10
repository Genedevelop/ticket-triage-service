from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.application.actions import execute
from backend.domain.ports import ProviderError, Providers, ToolSpec, TypedFailure, strict_schema

# the region a ticket is filed under when its customer record names none
DEFAULT_REGION = "global"


@dataclass(frozen=True)
class ToolContext:
    conversation_id: UUID
    customer_id: str | None
    turn_id: UUID
    now: datetime
    providers: Providers
    # the database the knowledge base is read from and the actions are written to
    pool: Any
    # where the customer is, as far as the ticket says
    region: str = DEFAULT_REGION
    # the executor's waits, provider time-out and lease
    timing: execute.Timing = execute.Timing()


class ToolError(TypedFailure):
    code = "tool-error"
    title = "Tool error"

    @classmethod
    def from_provider(cls, error: ProviderError) -> "ToolError":
        return cls(error.error_type, error.message, error.is_retryable)

    def as_result(self) -> dict[str, Any]:
        return {"error": {"type": self.error_type, "message": self.message, "retryable": self.is_retryable}}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[Any, ToolContext], dict[str, Any]]
    # an effect tool changes something outside the service
    is_effect: bool = False
    # the result lists `articles` whose ids a decision may cite
    has_citable_articles: bool = False

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=strict_schema(self.arguments))


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


def describe_validation_errors(error: ValidationError, unnamed: str = "") -> str:
    return "; ".join(f"{'.'.join(map(str, found['loc'])) or unnamed}: {found['msg']}" for found in error.errors())
