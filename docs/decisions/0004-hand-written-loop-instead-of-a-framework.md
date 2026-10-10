# 0004 — A hand-written loop instead of an agent framework

Source: `design.md` §0 D4 and §4.

## Context

The turn needs specific control points: a lease check before an effect tool is dispatched, an audit row per
step, one deadline for the whole turn, a step cap, one corrective round for invalid output, and a fallback
that still produces a decision. An approval belongs to a charge and must be found again from any
conversation or turn.

## Decision

The loop is written by hand in `backend/application/agent_loop.py`: `run_turn` sets the turn up, `_take_steps`
is the loop, and `_RunningTurn` holds the turn's state (`ask_model`, `run_tool_calls`, `decide`, `fall_back`,
`complete`). The model is reached through the `LLM` protocol in `backend/domain/ports.py`. The limits are
constants in that module: `TURN_DEADLINE_S`, `MODEL_TIMEOUT_S`, `MAX_STEPS`.

## Consequences

- The control points are ordinary code that tests can script against: `tests/integration/test_loop.py` covers invalid
  output, the step cap, the deadline, a retried model call and an oversized thread.
- Tool calls run one at a time, in the order recorded in the audit trail.
- The vendor SDK's own retries are switched off in the adapter; the loop retries a failed model call once.
- Nothing a framework ships — streaming, tracing, parallel tool calls — is available without writing it.

## Rejected

LangGraph, the OpenAI Agents SDK and Pydantic AI. None gives exactly-once effects: a checkpoint belongs to
one run of one conversation, while an approval belongs to a charge.
