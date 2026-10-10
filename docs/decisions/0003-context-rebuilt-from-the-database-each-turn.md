# 0003 — Each turn rebuilds its context from the database

Source: `design.md` §0 D3 and §8.

## Context

A conversation must survive a restart, every decision must be reconstructable afterwards, and neither may
depend on what the model vendor stores.

## Decision

Nothing about a conversation is kept in process memory or at the vendor. At the start of every turn
`agent_loop.run_turn` loads the conversation with `postgres/conversations.py::load`, and
`backend/application/model_input.py::build_input` builds the model input from it: the time now, the customer record,
the numbered thread, the previous decision, the proposed effects with their status now, and the conversation
with the operator. The OpenAI adapter sends `store: False`; each step resends its input.

Input over `MAX_INPUT_CHARS` is not truncated: the turn ends in a fallback toward a person.

## Consequences

- A restart loses nothing: `tests/integration/test_restart.py` reads everything back through a second application on the
  same database.
- Every model call and tool call is a row in `steps`, so `GET /conversations/{id}` shows how a decision was
  reached.
- The model sees an action's current status every turn, including a refund a person rejected.
- The whole thread is sent on every turn, so tokens grow with the thread.

## Rejected

The vendor's server-side conversation state, and an in-process session.
