import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from backend.application.actions import incident
from backend.application.actions.execute import Timing
from backend.application.customer import customer_record
from backend.application.model_input import MAX_INPUT_CHARS, Prompt, build_input
from backend.application.tools.registry import REGISTRY, citable_tool_names, read_tool_names, run_tool
from backend.application.tools.tool import DEFAULT_REGION, ToolContext, describe_validation_errors
from backend.core import logging as log
from backend.core.errors import Lost, Unavailable
from backend.domain import decision
from backend.domain.draft import DRAFT_SCHEMA, Draft, parse_draft
from backend.domain.ports import LLM, LLMMisconfigured, LLMReply, LLMUnavailable, Providers, ToolCall, ToolSpec
from backend.infrastructure.postgres import actions as stored_actions
from backend.infrastructure.postgres import conversations, turns

TURN_DEADLINE_S = 60.0
MODEL_TIMEOUT_S = 30.0
MAX_STEPS = 8
MS_PER_S = 1000
# the name a failed model step is recorded under while no call has reported a model id yet
UNKNOWN_MODEL = "unknown"


class TurnUnavailable(Unavailable):
    code = "model-unavailable"
    title = "Model unavailable"

    def __init__(self, conversation_id: UUID, reason: str) -> None:
        super().__init__(
            f"the model could not finish this turn ({reason}); no decision was written",
            details={"conversation_id": str(conversation_id)},
        )


# the clock deadlines and durations are measured on
monotonic = time.monotonic


def _elapsed_ms(started: float) -> int:
    return int((monotonic() - started) * MS_PER_S)


def _correction_request(error: ValidationError) -> str:
    problems = describe_validation_errors(error, unnamed="answer")
    return (
        "Your answer does not match the required output schema. Problems: " + problems
        + ". Answer again with the complete, corrected JSON object only."
    )


@dataclass
class _RunningTurn:
    pool: Any
    llm: LLM
    prompt: Prompt
    context: ToolContext
    is_operator: bool
    previous_decision: dict[str, Any] | None
    started: float
    steps: list[dict[str, Any]] = field(default_factory=list)
    # the model id the vendor reported at the last successful call
    model_id: str | None = None

    def record_step(self, **step: Any) -> None:
        self.steps.append(turns.record_step(self.pool, self.context.turn_id, **step))

    def actions_as_of_now(self) -> list[dict[str, Any]]:
        return stored_actions.for_conversation(self.pool, self.context.conversation_id)

    def ask_model(self, items: list[Any], tools: list[ToolSpec], time_left_s: float) -> LLMReply:
        # Step 1: Call the model with what is left of the turn deadline, at most the model time-out
        started = monotonic()
        try:
            reply = self.llm.respond(
                instructions=self.prompt.text,
                items=items,
                tools=tools,
                output_schema=DRAFT_SCHEMA,
                timeout_s=min(MODEL_TIMEOUT_S, time_left_s),
            )
        except LLMMisconfigured as error:
            # Step 2: Close the turn without a decision, because nothing is decided until the configuration is fixed
            self._record_failed_model_call(error, started, "misconfigured")
            turns.fail_turn(self.pool, self.context.conversation_id, self.context.turn_id)
            raise
        except LLMUnavailable as error:
            # Step 3: Record a call the model could not answer and leave the retry to the caller
            self._record_failed_model_call(error, started, "unavailable")
            raise
        # Step 4: Record the answered call in the audit trail and the log
        self.model_id = reply.model
        self.record_step(
            kind="model",
            name=reply.model,
            arguments=None,
            result={
                "tool_calls": [{"name": call.name, "arguments": call.arguments} for call in reply.tool_calls],
                "text": reply.text,
            },
            is_ok=True,
            duration_ms=_elapsed_ms(started),
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )
        log.event(
            "llm.call", outcome="ok", duration_ms=_elapsed_ms(started), model=reply.model, input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens, payload={"text": reply.text},
        )
        return reply

    def _record_failed_model_call(self, error: Exception, started: float, outcome: str) -> None:
        self.record_step(
            kind="model", name=self.model_id or UNKNOWN_MODEL, arguments=None, result={"error": str(error)}, is_ok=False,
            duration_ms=_elapsed_ms(started),
        )
        log.event("llm.call", outcome=outcome, duration_ms=_elapsed_ms(started), model=self.model_id)

    def run_tool_calls(self, calls: tuple[ToolCall, ...], items: list[Any]) -> None:
        for call in calls:
            # Step 1: Re-check the lease before an effect tool, because only the turn holding the conversation fires one
            tool = REGISTRY.get(call.name)
            if tool is not None and tool.is_effect and not turns.is_held_by(self.pool, self.context.conversation_id, self.context.turn_id):
                raise Lost(self.context.conversation_id)
            # Step 2: Run the tool and record the call in the audit trail and the log
            started = monotonic()
            arguments, result, is_ok = run_tool(call, self.context)
            self.record_step(kind="tool", name=call.name, arguments=arguments, result=result, is_ok=is_ok, duration_ms=_elapsed_ms(started))
            log.event(
                "tool.call", outcome="ok" if is_ok else "failed", duration_ms=_elapsed_ms(started), tool=call.name,
                payload={"arguments": arguments, "result": result},
            )
            # Step 3: Hand the result to the model as its next input item, in the vendor's shape
            items.append(self.llm.tool_result(call.call_id, result))

    def decide(self, draft: Draft) -> dict[str, Any]:
        # Step 1: Read the incidents this conversation created since its last decision, which the rules weigh
        incidents = stored_actions.created_since_last_decision(self.pool, self.context.conversation_id, incident.KIND)
        # Step 2: Store the decision assembled from the draft and the service's own records
        body = decision.from_draft(
            draft, self.steps, self.actions_as_of_now(), self.model_id, self.prompt.version, incidents,
            read_tools=read_tool_names(), citable_tools=citable_tool_names(),
        )
        return self.complete(body)

    def fall_back(self, reason: str) -> dict[str, Any]:
        # Step 1: Log why the turn ends without the model's decision
        log.event("turn.fallback", outcome=reason, duration_ms=_elapsed_ms(self.started))
        # Step 2: Fail an operator turn instead of writing a fallback, because the operator is there to ask again
        if self.is_operator:
            turns.fail_turn(self.pool, self.context.conversation_id, self.context.turn_id)
            raise TurnUnavailable(self.context.conversation_id, reason)
        # Step 3: Store the fallback decision that puts an ingested ticket in front of a person
        return self.complete(decision.fallback(reason, self.steps, self.actions_as_of_now(), self.model_id, self.prompt.version))

    def complete(self, body: dict[str, Any]) -> dict[str, Any]:
        # Step 1: Name the draft fields that differ from the previous decision version
        body["changed_fields"] = decision.changed_fields(self.previous_decision, body)
        # Step 2: Release the conversation and store the decision in one statement, raising Lost when it is not held
        stored = turns.complete_turn(
            self.pool, self.context.conversation_id, self.context.turn_id, body,
            reply_sent_at=self.context.now if self.is_operator else None,
        )
        # Step 3: Log the decision that was stored
        log.event(
            "decision.made", outcome=stored["next_action"], duration_ms=_elapsed_ms(self.started), urgency=stored["urgency"],
            version=stored["version"], source=stored["source"], overrides=[override["rule"] for override in stored["overrides"]],
        )
        return stored


def _take_steps(turn: _RunningTurn, items: list[Any]) -> dict[str, Any]:
    # Step 1: Describe every registered tool to the model and set the turn deadline
    tools = [tool.spec() for tool in REGISTRY.values()]
    deadline = monotonic() + TURN_DEADLINE_S
    has_asked_for_correction = False
    has_failed_last_call = False
    for _ in range(MAX_STEPS):
        # Step 2: Fall back once the turn deadline has passed
        time_left_s = deadline - monotonic()
        if time_left_s <= 0:
            return turn.fall_back("deadline_exceeded")
        # Step 3: Ask the model, trying a failed call once more before falling back
        try:
            reply = turn.ask_model(items, tools, time_left_s)
        except LLMUnavailable as error:
            if has_failed_last_call:
                return turn.fall_back(f"model_unavailable: {error}")
            has_failed_last_call = True
            continue
        has_failed_last_call = False
        items.extend(reply.carry)
        # Step 4: Run the tools the model called and go round again with their results
        if reply.tool_calls:
            turn.run_tool_calls(reply.tool_calls, items)
            continue
        # Step 5: Parse the final answer, allowing one corrective round with the validation errors
        try:
            draft = parse_draft(reply.text)
        except ValidationError as error:
            if has_asked_for_correction:
                return turn.fall_back("invalid_output")
            has_asked_for_correction = True
            items.append(turn.llm.user_message(_correction_request(error)))
            continue
        # Step 6: Store the decision of a valid draft
        return turn.decide(draft)
    # Step 7: Fall back when the model used every step without a final answer
    return turn.fall_back("step_limit")


def run_turn(
    *,
    pool: Any,
    llm: LLM,
    providers: Providers,
    prompt: Prompt,
    conversation_id: UUID,
    turn_id: UUID,
    now: datetime,
    is_operator: bool = False,
    timing: Timing = Timing(),
) -> dict[str, Any]:
    # Step 1: Start the turn's clock and load everything stored about the conversation
    turn_started = monotonic()
    log.event("turn.started", outcome="running", kind="operator" if is_operator else "ingest")
    conversation = conversations.load(pool, conversation_id)
    # Step 2: Fix what the tools know and the model cannot influence: the ids, the customer, the region, the time
    context = ToolContext(
        conversation_id=conversation_id,
        customer_id=conversation["customer_id"],
        turn_id=turn_id,
        now=now,
        providers=providers,
        pool=pool,
        region=str(customer_record(conversation).get("region") or DEFAULT_REGION).strip().lower(),
        timing=timing,
    )
    # Step 3: Gather the running turn's state: the model, the prompt, the previous decision and the clock
    turn = _RunningTurn(
        pool=pool,
        llm=llm,
        prompt=prompt,
        context=context,
        is_operator=is_operator,
        previous_decision=conversation["decisions"][-1] if conversation["decisions"] else None,
        started=turn_started,
    )
    # Step 4: Fall back without a model call when the thread is too large for the model
    turn_input = build_input(conversation, now)
    if len(turn_input) > MAX_INPUT_CHARS:
        return turn.fall_back("input_too_large")
    # Step 5: Let the model and the tools take steps until a decision is stored
    return _take_steps(turn, [llm.user_message(turn_input)])
