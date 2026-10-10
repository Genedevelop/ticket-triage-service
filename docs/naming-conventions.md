# Naming conventions

One case per kind of name, across the whole repository.

| What | Case | Examples from this repository |
|---|---|---|
| Python packages and modules | `snake_case` | `backend/application/agent_loop.py`, `backend/infrastructure/postgres/status_page_fixtures.py` |
| Functions, methods, variables, parameters | `snake_case` | `run_turn`, `start_operator_turn`, `incidents_since_last_decision` |
| Classes, exceptions, protocols | `PascalCase` | `ToolContext`, `OpenAIResponsesLLM`, `EvidenceUnreadable`, `StatusPage` |
| Constants | `UPPER_SNAKE_CASE` | `TURN_DEADLINE_S`, `RELEASE_AND_WRITE`, `POLICY`, `REGISTRY` |
| Private helpers | `_leading_underscore` | `_take_steps`, `_RunningTurn`, `_message_key` |
| Test functions | `test_` + a sentence in `snake_case` | `test_a_persons_rejection_stays` |
| Files under `docs/` | `kebab-case.md` | `eval-baseline.md`, `adding-a-feature.md`, `decisions/0002-effect-identity-is-the-object-acted-on.md` |
| Conventional root files | their conventional name | `README.md`, `CLAUDE.md`, `Dockerfile`, `compose.yaml` |
| Prompt files | `snake_case` with a version | `triage_v2.md`; the stem is stored on every decision as `triage_v2@<hash>` |
| JSON fields, request and response | `snake_case` | `conversation_id`, `next_action`, `changed_fields` |
| SQL tables, columns, indexes | `snake_case` | `action_events`, `busy_until`, `actions_live_key` |
| Log event names | two lower-case words joined by a dot: the subject, then what happened | `turn.started`, `decision.made`, `tool.call` |
| Log keys | `snake_case` | `request_id`, `duration_ms` |
| URL paths | lower case, resources in the plural, path parameters in `snake_case` | `/tickets`, `/conversations/{conversation_id}/messages` |
| HTTP headers | `Title-Case` | `X-Request-Id`, `Idempotency-Key`, `Idempotent-Replayed` |
| Environment keys | `UPPER_SNAKE_CASE`, service-specific ones prefixed `TRIAGE_` | `TRIAGE_MODEL`, `DATABASE_URL` |
| Error codes and problem type slugs | `kebab-case` | `conversation-busy`, `urn:ticket-triage:problem:turn-lost` |
| Tool names, effect kinds, action statuses | `snake_case` strings | `propose_refund`, `incident`, `pending_approval` |

## Rules that go beyond case

**A name says what the thing is, so it needs no docstring.** When a name needs a sentence beside it, rename it.

| Bad (what the code had) | Good (what it has) |
|---|---|
| `_ms(started)` | `_elapsed_ms(started)` |
| `_again(...)` | `_answer_repeated_verdict(...)` |
| `_returned_article_ids(steps)` | `_article_ids_returned_by_search(steps)` |
| `fixtures.py` | `status_page_fixtures.py` |
| `MockStatusPage.set_page(...)` | `MockStatusPage.add_page(...)`: it inserts once and never overwrites |

**A boolean reads as a predicate**: `is_`, `has_`, `can_`.

| Bad | Good |
|---|---|
| `run_turn(operator=True)` | `run_turn(is_operator=True)` |
| `Tool.effect` | `Tool.is_effect` |
| `Outcome.created` | `Outcome.is_created` |
| `corrected` | `has_asked_for_correction` |
| `ProviderError.retryable` | `ProviderError.is_retryable` |
| `Policy.hold_key` | `Policy.is_key_held` |
| `Case.keep_previous` | `Case.is_previous_kept` |
| `db.ping(pool)` | `db.is_reachable(pool)` |
| `turns.holds(pool, conversation_id, turn_id)` | `turns.is_held_by(pool, conversation_id, turn_id)` |

**No abbreviation that has to be decoded.** `cursor`, not `cur`; `environment`, not `env`; `call`, not `c`.
Accepted because everyone reads them the same way: `conn`, `id`, `kb`, `llm`, `sql`, and unit suffixes
(`_s`, `_ms`).

**The same thing has the same name everywhere.** `pool`, `providers`, `timing`, `now`, `conversation_id`
and `turn_id` keep those names from the route down to the store function.

**Modules are named after what they hold, not after a pattern.** `approve.py`, `execute.py`, `resolve.py`,
`refund.py` — not `service.py`, `manager.py`, `utils.py`.

**An error class name is unique across `backend/`** and its `code` is the kebab-case form of what happened:
`NotInReview` / `action-not-in-review`.

**Never in a name or a comment**: task ids, ticket keys, design section numbers.

## Names kept on purpose

Some names do not follow the predicate rule, most because something outside the code reads them:

- `retryable` and `type` in a tool's error result (`ToolError.as_result`) are JSON keys the model reads. The
  attributes behind them are `is_retryable` and `error_type`, and the `details` of a `TypedFailure` use those
  attribute names (`error_type`, `is_retryable`); no client reads them, because a `ProviderError` and a
  `ToolError` are caught before they reach HTTP.
- `hold_key` is a column of `actions`; the field on `Policy` and the argument of `store.propose` are
  `is_key_held`.
- `parked`, `per_turn` and `approver` on `Policy`, and `Busy`, `Lost`, `OtherCustomer`, are asserted by name
  in the tests.
- `ok` on a step is a column of `steps` and a key of `tools_called` in the decision; the argument of
  `turns.record_step` is `is_ok`.
- R1 to R4 are the rule names stored in a decision's `overrides`.
- `decide`, `record_outcome`, `release`, `park` and `resolve` in `backend/infrastructure/postgres/actions.py`
  return a boolean and are not named as predicates. Each is a compare-and-set command that returns whether
  the row moved; a predicate name would hide the write.

## How it is checked

- Error codes are kebab-case and error class names are unique: `tests/architecture/test_error_model.py`.
- Log event names and keys: `tests/integration/test_log_events.py`.
- JSON fields of the decision: the strict schema derived from `Draft` forbids anything else.
- Everything else is checked by review. There is no linter in `requirements.txt`.
