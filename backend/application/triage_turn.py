from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.application.actions.execute import Timing
from backend.application.agent_loop import run_turn
from backend.application.customer import split_customer
from backend.application.model_input import Prompt
from backend.core import logging as log
from backend.core.errors import Misconfigured
from backend.domain import decision
from backend.domain.ports import LLM, LLMMisconfigured, Providers
from backend.infrastructure.postgres import conversations, turns

# seconds a turn holds its conversation; the longest turn runs about 95 s
LEASE_S = 120.0


class ModelMisconfigured(Misconfigured):
    code = "model-misconfigured"
    title = "Model misconfigured"

    def __init__(self, conversation_id: UUID, detail: str) -> None:
        super().__init__(
            f"the model rejected the service's configuration: {detail}; the request is stored and nothing was decided",
            details={"conversation_id": str(conversation_id)},
        )


@dataclass(frozen=True)
class Runner:
    pool: Any
    llm: LLM
    providers: Providers
    prompt: Prompt
    timing: Timing


@dataclass(frozen=True)
class Outcome:
    conversation_id: UUID
    decision: dict[str, Any]
    is_created: bool = False
    # whether the decision was stored by an earlier request
    is_replayed: bool = False


def abandoned_fallback(prompt_version: str) -> turns.Fallback:
    def build(steps: list[dict[str, Any]], actions: list[dict[str, Any]], previous: dict[str, Any] | None) -> dict[str, Any]:
        # Step 1: Take the model id from the last model call the dead turn completed
        model = next((step["name"] for step in reversed(steps) if step["kind"] == "model" and step["ok"]), None)
        # Step 2: Build the fallback decision that puts the dead turn's ticket in front of a person
        body = decision.fallback("turn_abandoned", steps, actions, model, prompt_version)
        body["changed_fields"] = decision.changed_fields(previous, body)
        return body

    return build


def clean_up_at_start_up(pool: Any, prompt: Prompt) -> None:
    turns.clean_up_dead_turns(pool, abandoned_fallback(prompt.version))


def _run(runner: Runner, started: conversations.Started, now: datetime, *, is_operator: bool) -> Outcome:
    # Step 1: Answer from the stored decision when the request opened no turn
    if started.turn_id is None:
        return Outcome(started.conversation_id, started.replayed_decision, is_replayed=True)
    # Step 2: Run the turn with its ids on every log line, reporting a rejected configuration as a service error
    try:
        with log.bound(conversation_id=started.conversation_id, turn_id=started.turn_id):
            stored = run_turn(
                pool=runner.pool,
                llm=runner.llm,
                providers=runner.providers,
                prompt=runner.prompt,
                conversation_id=started.conversation_id,
                turn_id=started.turn_id,
                now=now,
                is_operator=is_operator,
                timing=runner.timing,
            )
    except LLMMisconfigured as error:
        raise ModelMisconfigured(started.conversation_id, str(error)) from error
    return Outcome(started.conversation_id, stored, is_created=started.is_created)


def ingest(
    runner: Runner,
    now: datetime,
    *,
    ticket_id: str | None,
    customer: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> Outcome:
    # Step 1: Store the thread and open a turn, unless a decision that is no fallback covers its last message
    customer_id, customer_metadata = split_customer(customer)
    started = conversations.start_ingest(
        runner.pool,
        ticket_id=ticket_id,
        customer_id=customer_id,
        customer_metadata=customer_metadata,
        messages=messages,
        lease_s=LEASE_S,
        fallback=abandoned_fallback(runner.prompt.version),
    )
    # Step 2: Triage the thread, or answer with the decision already stored
    return _run(runner, started, now, is_operator=False)


def answer_operator(runner: Runner, now: datetime, conversation_id: UUID, body: str, idempotency_key: str | None) -> Outcome | None:
    # Step 1: Store the operator's message once per key and open a turn on it
    started = conversations.start_operator_turn(
        runner.pool,
        conversation_id,
        body,
        now,
        idempotency_key=idempotency_key,
        lease_s=LEASE_S,
        fallback=abandoned_fallback(runner.prompt.version),
    )
    # Step 2: Report a conversation that does not exist as None
    if started is None:
        return None
    # Step 3: Run the turn, or answer with the decision already stored for this key
    return _run(runner, started, now, is_operator=True)
