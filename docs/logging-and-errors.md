# Logging and errors

Two things every layer shares, both in `backend/core`: one logger (`logging.py`) and one error hierarchy
(`errors.py`).

## Logging

### The API

```python
from backend.core import logging as log

log.configure(level)                  # once, in create_app; an unknown level raises UnknownLogLevel
with log.bound(conversation_id=...):  # ids carried on every line logged inside the block
    log.event("tool.call", outcome="ok", duration_ms=12, tool="kb_search", payload={...})
```

`event` writes one JSON object per line to standard output. No other module under `backend/` may import the
standard `logging` module, and `print` is allowed only under `backend/cli`, where it is a command's output
(`backend/cli/reset.py`); `tests/architecture/test_logging_rule.py` enforces both. `evals/` is outside `backend/` and prints its
tables and its smoke check.

### Keys on every line

| Key | Value |
|---|---|
| `ts` | UTC time, ISO 8601 |
| `event` | the event name |
| `request_id` | the caller's `X-Request-Id`, or a new id; bound by the middleware in `backend/api/app.py` |
| `conversation_id`, `turn_id` | bound by `triage_turn._run` for the length of a turn; `null` outside one |
| `action_id` | set on `action.transition`; `null` elsewhere |
| `duration_ms` | integer, or `null` |
| `outcome` | what happened, per event below |

### Events

| Event | Written in | `outcome` | Other keys |
|---|---|---|---|
| `turn.started` | `agent_loop.run_turn` | `running` | `kind`: `ingest` or `operator` |
| `llm.call` | `agent_loop._RunningTurn.ask_model` | `ok`, `unavailable` or `misconfigured` | `model`; on `ok` also `input_tokens`, `output_tokens` |
| `tool.call` | `agent_loop._RunningTurn.run_tool_calls` | `ok` or `failed` | `tool` |
| `action.transition` | `postgres/actions.py::_write_event` | the action's new status, or `unknown` for an attempt whose outcome is not known | `transition` (`proposed`, `approved`, `rejected`, `stale`, `claimed`, `provider_result`, `parked`, `resolved`), `actor` |
| `decision.made` | `agent_loop._RunningTurn.complete` | the decision's `next_action` | `urgency`, `version`, `source`, `overrides` (rule names) |
| `turn.fallback` | `agent_loop._RunningTurn.fall_back` | the reason: `deadline_exceeded`, `step_limit`, `invalid_output`, `input_too_large`, or `model_unavailable: <vendor error>` | — |

Three things the events do not cover:

- A `joined` action event writes no `action.transition` line, because the action's status did not change.
- The fallback decision written for a dead turn (`turn_abandoned`) is written by the store during clean-up
  and logs nothing.
- An unhandled failure answered with `500` has no event; the server's traceback is its record.

`action.transition` is written when the event row is inserted, inside the transaction that changes the
status.

### What is never logged

- At the default level (`INFO`): message bodies, tool arguments, tool results and the model's text. They
  are passed as `payload`, which `event` writes only at `DEBUG`. They are stored as `steps` and read with
  `GET /conversations/{conversation_id}`. `tests/integration/test_log_events.py` asserts their absence.
- At any level: the API key. The OpenAI adapter describes a vendor error by its class name, HTTP status
  and code only (`openai_responses._describe`).
- At any level: an approver's or resolver's note. The decision's rationale and reply drafts are written only
  at `DEBUG`, inside the `payload` of `llm.call`, which carries the model's text.

An approver's or resolver's name **is** logged, as `actor` on `action.transition`.

`TRIAGE_LOG_LEVEL` sets the level. Above `INFO` no event is written at all.

### Adding an event

Call `log.event("<subject>.<what_happened>", outcome=..., ...)` where it happens; put anything that may
carry customer text in `payload`. Add the name to the tables here and in the root `README.md`, and assert it
in `tests/integration/test_log_events.py`.

## Errors

### The hierarchy

Every exception defined under `backend/` is an `AppError` of exactly one category. The categories are in
`backend/core/errors.py`; a specific error lives in the module that raises it. The status comes from the
category alone, through `STATUS` in `backend/api/problems.py`. Two store functions raise the category
`Internal` itself, for a state no ordinary interleaving reaches: `postgres/actions.py::propose` and
`postgres/incident_fixtures.py::open_or_attach`, each after its last round.

| Category | Status | Error | Defined in | Code | Reaches the client? |
|---|---|---|---|---|---|
| `NotFound` | 404 | `NotFound("conversation")`, `NotFound("action")` | `core/errors.py`, raised by the routes | `not-found` | yes |
| `Conflict` | 409 | `Busy` | `core/errors.py`, raised by `postgres/turns.py` | `conversation-busy` | yes |
| | | `Lost` | `core/errors.py`, raised by `postgres/turns.py` and `agent_loop.py` | `turn-lost` | yes |
| | | `OtherCustomer` | `core/errors.py`, raised by `postgres/conversations.py` | `ticket-customer-mismatch` | yes |
| | | `Contradiction` | `application/actions/approve.py` | `verdict-contradiction` | yes |
| | | `NotInReview` | `application/actions/resolve.py` | `action-not-in-review` | yes |
| `Invalid` | 422 | `NoUsableToken` | `infrastructure/postgres/kb.py` | `no-usable-token` | no: `kb_search` turns it into a `ToolError` |
| `Denied` | 403 | `RefundDenied` | `application/actions/refund.py` | `refund-denied` | no: `propose_refund` returns it to the model |
| | | `IncidentDenied` | `application/actions/incident.py` | `incident-denied` | no: `open_incident` returns it to the model |
| `Unavailable` | 503 + `Retry-After: 5` | `TurnUnavailable` | `application/agent_loop.py` | `model-unavailable` | yes, on an operator turn |
| | | `EvidenceUnreadable` | `application/actions/approve.py` | `evidence-unreadable` | yes |
| | | `LLMUnavailable` | `domain/ports.py` | `llm-unavailable` | no: the loop retries once, then falls back |
| `Misconfigured` | 503, no `Retry-After` | `ModelMisconfigured` | `application/triage_turn.py` | `model-misconfigured` | yes |
| | | `SettingMissing` | `core/config.py` | `setting-missing` | no: raised at start-up, before the service answers |
| | | `UnknownLogLevel` | `core/logging.py` | `unknown-log-level` | no: raised at start-up, before the service answers |
| | | `LLMMisconfigured` | `domain/ports.py` | `llm-misconfigured` | no: re-raised as `ModelMisconfigured` |
| `CallFailed` | 502 | `TypedFailure` | `domain/ports.py` | `typed-failure` | no: the base of the two below, never raised itself |
| | | `ProviderError` | `domain/ports.py` | `provider-error` | no: caught by the tools, the executor and the approval |
| | | `ToolError` | `application/tools/tool.py` | `tool-error` | no: `run_tool` returns it to the model |
| `Internal` | 500, plain text | `IllegalTransition` | `domain/action_status.py` | `illegal-transition` | as the server's plain 500 |
| | | `SimulatedCrash` | `infrastructure/providers/faults.py` | `simulated-crash` | as the server's plain 500; tests only |
| | | `UnknownFault` | `infrastructure/providers/faults.py` | `unknown-fault` | as the server's plain 500; tests only |
| | | `ScriptExhausted` | `infrastructure/llm/scripted.py` | `script-exhausted` | as the server's plain 500; tests only |
| | | `ReservedDetail` | `core/errors.py` | `reserved-detail` | as the server's plain 500 |
| | | `EvidenceCheckMissing` | `application/actions/approve.py` | `evidence-check-missing` | as the server's plain 500; the action stays pending |
| | | `EffectUnregistered` | `application/actions/registry.py` | `effect-unregistered` | as the server's plain 500; nothing is approved or executed |

`Unavailable` means trying again may help, so the response carries `Retry-After`. `Misconfigured` means a
person must change a key, a model id or an account first, so it does not.

An error has four fields: `code` (class attribute, kebab-case), `title` (class attribute), `message` and
`details` (a dictionary). A subclass's constructor builds the message and the details from what it knows —
see `Busy` in `backend/core/errors.py`.

No module under `backend/` raises an exception class that is not an `AppError`, as far as the source alone
names the class. `tests/architecture/test_error_model.py` reads every `raise` and fails on three forms: a built-in name
(`raise RuntimeError(...)`), a built-in through `builtins` (`raise builtins.ValueError`), and a class reached
through the module's own import statements (`raise json.JSONDecodeError(...)`, or the same class imported by
name). It does not see a class bound to a local name or returned by a call, and a re-raise (`raise`,
`raise error`) is not a new raise. A mistake in a test's set-up is an `Internal` error too (`UnknownFault`,
`ScriptExhausted`).

`ProviderError` and `ToolError` share one base, `TypedFailure` (`domain/ports.py`): a failure with an
`error_type` the caller branches on and `is_retryable`, which says the outcome is unknown. Its `details`
carry the same two names. The result a tool hands the model keeps its own keys, `type`, `message` and
`retryable` (`ToolError.as_result`).

Two `Internal` errors make the effect path fail closed. `EvidenceCheckMissing`: a person approves an action
whose kind has no `changed_evidence_fields` in the effect registry. `EffectUnregistered`: the kind has no row in
the registry at all. Neither approves or executes anything; `tests/unit/test_effect_registry.py` keeps both from
happening with the kinds the service ships.

### How a failure reaches the client

1. Code raises an `AppError` subclass where the failure is found — often in the store, inside a transaction
   that rolls back.
2. Nothing between there and the route translates it. Application code catches an error only to act on it.
3. The route does not catch it. A route only raises `NotFound` when the application returns `None`.
4. `backend/api/problems.py::install` registered one handler for every category except `Internal`. It answers
   with `problem_for(error)`:

   ```json
   {
     "type": "urn:ticket-triage:problem:conversation-busy",
     "title": "Conversation busy",
     "status": 409,
     "detail": "another turn is running on this conversation; send the request again when it has finished",
     "conversation_id": "..."
   }
   ```

   `type` is the prefix plus `code`, `detail` is `message`, and every entry of `details` becomes a member of
   the body. The media type is `application/problem+json`.
5. An `Internal` error, an `AppError` with no category, or any other exception has no handler. The server
   answers it as it did before the hierarchy existed: `500`, `text/plain`, body `Internal Server Error`,
   and logs the traceback. Nothing from the exception is in the body.
6. The framework's own errors use the same media type: a body that fails validation is `422` with type
   `urn:ticket-triage:problem:validation-error` and an `errors` list of `location`, `pointer` and `detail`;
   an unknown path or a refused method keeps its status with type `about:blank`.

Checked by `tests/architecture/test_error_model.py` and `tests/integration/test_problem_json.py`.

### Adding an error

1. Choose the category by what the caller should do: fix the request (`Invalid`), wait (`Unavailable`), look
   at the current state (`Conflict`), and so on.
2. Define the class in the module that raises it:

   ```python
   class NotInReview(Conflict):
       code = "action-not-in-review"
       title = "Action is not waiting for a resolution"

       def __init__(self, action: dict[str, Any]) -> None:
           super().__init__(
               f"the action is {action['status']}; only an action in needs_review can be resolved",
               details={"action_id": str(action["id"]), "action_status": action["status"]},
           )
   ```

   The class name must be unique under `backend/`. The keys of `details` are written into the same JSON object
   as the members of the problem document, so `AppError` refuses `type`, `title`, `status` and `detail` as
   keys with `ReservedDetail`.
3. Raise it. No route and no handler changes.
4. `tests/architecture/test_error_model.py` picks the class up by itself. Add a test of the response where a client can
   meet it, like `tests/integration/test_problem_json.py`.

A new **category** is a class in `backend/core/errors.py` and a row in `STATUS`;
`test_every_category_maps_to_exactly_one_status` fails until both exist. Only `Unavailable` gets
`Retry-After` (`problems.problem_for`).

An error meant for the model, not for HTTP, is still an `AppError`: raise `ToolError(error_type, message,
is_retryable)` from a tool handler and `run_tool` returns it as the tool's result.
