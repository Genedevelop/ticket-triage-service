# Architecture

How the code under `backend/` and the composition root `main.py` beside it are laid out, which package may
import which, how a request travels through it, and where state lives. The reasoning behind the design is in
`design.md`; the decisions are in `decisions/`.

## Layers

```
                    main.py       composition root, at the repository root: builds the pool, the model adapter, the providers
                    backend/cli       command-line entry points: build infrastructure as the root does
                         │
        ┌────────────────┼──────────────────────────────┐
        ▼                ▼                              ▼
     backend/api ──▶ backend/application ──▶ backend/infrastructure/postgres
                         │                     backend/infrastructure/llm        ┐ reached only through the
                         │                     backend/infrastructure/providers  ┘ ports in backend/domain/ports.py
                         ▼                              │
                    backend/domain ◀───────────────────────┘
                         │
                         ▼
                     backend/core
```

| Package | Holds | May import from this repository |
|---|---|---|
| `backend/core` | `config.py` (settings read once; defines `SettingMissing`), `logging.py` (the one JSON logger; defines `UnknownLogLevel`), `errors.py` (the error base, the eight categories, `ReservedDetail` with `RESERVED_DETAIL_KEYS`, and the three conflicts the store raises) | nothing outside itself |
| `backend/domain` | `draft.py` (what the model fills in), `policy.py` (who approves each effect kind), `effect_keys.py` (effect identity), `action_status.py` (the state machine), `rules.py` (the ranked next actions and the floors R1 to R4), `decision.py` (the decision assembled from a draft and the service's records, and the fallback), `ports.py` (the `LLM`, `Billing`, `StatusPage` and `Incidents` protocols, the `Providers` bundle, the two model errors, `TypedFailure` and `ProviderError`, the strict-schema builder) | itself, `backend.core` |
| `backend/application` | `triage_turn.py` (ingest and operator turns), `agent_loop.py` (the turn loop), `model_input.py` (prompt and model input), `customer.py` (the customer record split and joined, without the store), `conversations.py`, `health.py`, `prompts/` | itself, `backend.domain`, `backend.core`, `backend.infrastructure.postgres` |
| `backend/application/tools` | `tool.py` (`Tool`, `ToolContext`, `ToolError`), `registry.py`, one module per tool | same as `backend/application` |
| `backend/application/actions` | `approve.py`, `execute.py`, `resolve.py`, one module per effect kind (`refund.py`, `incident.py`), `registry.py` | same as `backend/application` |
| `backend/infrastructure/postgres` | `db.py`, `schema.sql`, one module per aggregate (`conversations.py`, `turns.py`, `actions.py`, `kb.py`), the mocks' tables (`billing_fixtures.py`, `status_page_fixtures.py`, `incident_fixtures.py`), `reset.py` (the function that rebuilds the schema) | itself, `backend.domain`, `backend.core` |
| `backend/infrastructure/llm` | `openai_responses.py` (the real adapter), `scripted.py` (the model used by tests) | itself, `backend.domain`, `backend.core` |
| `backend/infrastructure/providers` | `billing.py`, `status_page.py`, `incidents.py` (the mocks' contract logic), `faults.py` (the fault modes a test queues), `sample_data.py` (the three sample tickets, the provider state behind them, and the loaders) | itself, `backend.infrastructure.postgres`, `backend.domain`, `backend.core` |
| `backend/api` | `app.py` (the FastAPI application and the request-id middleware), `problems.py` (errors to problem+json), `runner.py` (`build_runner`, the turn runner from the wiring), `routes/` with one module per resource | itself, `backend.application`, `backend.domain`, `backend.core` |
| `main.py` (repository root, outside the package) | `create_app()`: wiring and start-up | every package under `backend` except `backend.cli`; nothing under `backend` imports it |
| `backend/cli` | command-line entry points: `reset.py` (`python -m backend.cli.reset`) | everything under `backend`; nothing under `backend` imports it |

`rules.py` and `decision.py` ask the tool registry nothing. The loop passes them the two answers they need as
plain names: which tools are read tools (`tools/registry.py::read_tool_names`) and which return articles a
decision may cite (`citable_tool_names`).

## Dependency rules

All of them are enforced by `tests/architecture/test_import_rule.py`, which parses the imports of every module under
`backend/` and of `main.py`.

1. **Layers.** The `LAYERS` constant of the test holds these rows: the two `application` sub-packages fall
   under `backend.application`, and a bare `backend.infrastructure` row covers that package's own marker. A module's
   imports of `backend`, `main`, `evals` or `tests` must fall inside its layer's list, and a module's layer is
   the longest row name it lies under. No row lists `evals`, `tests` or `main`, so no module under `backend/`
   imports them — `backend/cli` included. No module is unrestricted: `backend/__init__.py`, which lies under
   no row, may import nothing at all.
2. **The store is not behind a port.** `backend.application` imports `backend.infrastructure.postgres` directly and
   never `backend.infrastructure.llm` or `backend.infrastructure.providers`; the model and the providers arrive as
   the protocols of `backend/domain/ports.py`. See `decisions/0006-tests-on-real-postgresql-without-a-store-double.md`.
3. **The transport never touches infrastructure.** `backend.api` does not import `backend.infrastructure`; a route
   that needs stored data calls an application function (`backend/application/health.py` and
   `backend/application/conversations.py` exist for this).
4. **One owner per external library** (the `OWNERS` constant): `fastapi` and `starlette` only under `backend/api`,
   `psycopg` and `psycopg_pool` only under `backend/infrastructure/postgres`, `openai` only under
   `backend/infrastructure/llm`. This rule has no exemption and covers `main.py` too, which imports none of them. A library
   with no owner fails the test anywhere under `backend/`, except `pydantic`, which every layer may use.
5. **The domain is pure.** Modules under `backend/domain` import only `pydantic`, `backend.core`, `backend.domain` and
   the standard-library modules listed in `DOMAIN_STDLIB`. The list is fixed and holds no module for files,
   the network, the environment or the system clock; it does hold `datetime` and `uuid`.
6. **Every module belongs to a layer.** A new package under `backend/` fails the test until it is added to
   `LAYERS`. The rule has no exemption: the composition root is not under `backend/`.
7. **Infrastructure packages stand alone.** `postgres` and `llm` import no other infrastructure package;
   `providers` may import `postgres`, where the mocks keep their state.
8. **No import by a name built at run time.** `importlib`, `pkgutil`, `runpy` and `__import__` are refused
   under `backend/` and in `main.py`, because the rules above read import statements.
9. **Standard-library clients stay in infrastructure.** The network and database clients of the standard
   library (the `STDLIB_CLIENTS` constant: `urllib.request`, `http.client`, `http.server`, `socket`,
   `socketserver`, `ssl`, `sqlite3`, `dbm`, `smtplib`, `ftplib`, `poplib`, `imaplib`, `xmlrpc`) are refused
   outside `backend/infrastructure`, `main.py` included.
10. **Commands are leaves.** `backend/cli` holds command-line entry points. Like `main.py` they construct
    infrastructure, so they may import everything under `backend`; no row lists `backend.cli`, so nothing under
    `backend/` imports a command, and neither does `main.py`.
11. **The composition root stands outside the package.** `main.py` is the module `main` at the repository
    root (the `ROOT_MODULE` constant). It may import the five layers (`ROOT_MODULE_IMPORTS`) and never
    `backend.cli`, `evals` or `tests`; nothing under `backend/` imports it.

Each rule has a synthetic negative: source text that breaks the rule, given to the same checker function
the rule's real test calls, which must report it.

Limits of the rules, stated plainly:

- They cover `backend/` and `main.py` only. `evals/` and `tests/` import `fastapi.testclient` and `psycopg` directly.
- They read imports, not calls. `datetime` and `uuid` are allowed in the domain, so `datetime.now()` or
  `uuid.uuid4()` there would pass; today no domain module calls either.
- A cycle between two modules of one layer is not looked for.
- Rule 9 matches the listed module names: `import urllib` alone, or a client module that is not listed,
  would pass.

Four more structure tests sit beside it in `tests/architecture`.
`tests/architecture/test_error_model.py`: every exception class under `backend/` or in `main.py` is an
`AppError` of exactly one category, and no module raises a class that is not an `AppError` where the source
alone names the class (see `logging-and-errors.md` for what that covers).
`tests/architecture/test_logging_rule.py`: only `backend/core/logging.py` imports the standard logger, and
`print` is called only there and under `backend/cli`; `main.py` is held to the same.
`tests/architecture/test_package_markers.py`: every directory of modules under `backend/` is a regular package
whose `__init__.py` opens with one comment line saying what the package is.
`tests/architecture/test_customer_record.py`: the module that splits and joins the customer record imports
nothing from the store.

## Request paths

Every request first passes `carry_request_id` in `backend/api/app.py`, which binds the caller's `X-Request-Id`
(or a new id) to every log line and returns it in the response header.

### Ticket ingest — `POST /tickets`

1. `backend/api/routes/tickets.py::ingest_ticket` — FastAPI validates the body against `TicketIn`; the route
   passes the customer record and the messages to `triage_turn.ingest`.
2. `backend/application/triage_turn.py::ingest` separates the customer id from the rest of the customer record
   (`customer.split_customer`) and calls `postgres/conversations.py::start_ingest`, which in one
   transaction finds or creates the conversation (`OtherCustomer` if the ticket id is stored under another
   customer id), stores the messages not seen before, and either returns the stored decision — when a
   decision that is not a fallback covers the last message — or opens a turn with
   `postgres/turns.py::open_turn`. Opening a turn claims the conversation's lease (`Busy` if another turn
   holds it) and cleans up any turn that died holding it.
3. `triage_turn._run` binds the conversation and turn ids to the log and calls `agent_loop.run_turn`.
4. `backend/application/agent_loop.py::run_turn` loads the conversation from the database, fixes the
   `ToolContext` (ids, customer, region read from `customer.customer_record`, time, providers, pool), and
   builds the model input with `model_input.build_input`. Input over `MAX_INPUT_CHARS` ends the turn in a
   fallback without a model call.
5. `agent_loop._take_steps` loops under one deadline (`TURN_DEADLINE_S`) and a step cap (`MAX_STEPS`):
   it calls the model through the `LLM` port; for each tool call it re-checks the lease before an effect
   tool (`turns.is_held_by`), runs the tool through `tools/registry.py::run_tool`, and records the step with
   `turns.record_step`.
6. On a final answer `domain/draft.py::parse_draft` validates it (one corrective round, then fallback),
   `backend/domain/decision.py::from_draft` adds what the service recorded and applies
   `backend/domain/rules.py::apply` — the loop passes both the names of the read tools and of the tools whose
   articles may be cited — and
   `turns.complete_turn` releases the conversation and writes the decision in one statement (`Lost` if the
   turn no longer holds the conversation).
7. The route answers `201` for a new conversation and `200` otherwise, with `Idempotent-Replayed: true` when
   the decision came from store.

When the model cannot finish, an ingest turn stores a fallback decision (`source: fallback`,
`next_action: escalate_to_human`) and still answers. When the model is misconfigured the turn is closed as
failed and `ModelMisconfigured` answers `503`; the ticket is stored.

### Operator message — `POST /conversations/{conversation_id}/messages`

1. `backend/api/routes/conversations.py::operator_message` validates `OperatorMessageIn`, reads the optional
   `Idempotency-Key` header and calls `triage_turn.answer_operator`. A `None` result becomes `NotFound`.
2. `postgres/conversations.py::start_operator_turn`, in one transaction: looks the message up under its key;
   returns the stored decision if an operator turn already covered it; otherwise opens the turn first and
   stores the message second, so a busy conversation stores nothing.
3. Steps 3 to 6 of the ingest path run unchanged with `is_operator=True`. Two things differ, both in
   `agent_loop._RunningTurn`: a turn the model cannot finish is closed with `turns.fail_turn` and raises
   `TurnUnavailable` (`503` with `Retry-After`) instead of writing a fallback, and a completed turn stores
   the reply as an `assistant` message in the same statement as the decision.
4. The route answers `200` with `conversation_id`, `reply` and `decision`.

### Approval — `POST /actions/{action_id}/approval`

1. `backend/api/routes/actions.py::approve_action` validates `ApprovalIn` and calls
   `backend/application/actions/approve.py::decide`. A `None` result becomes `NotFound`.
2. `approve.decide` reads the action's status first. An action that already has a verdict goes to
   `_answer_repeated_verdict`: the same verdict answers with the current status and drives an unfinished
   execution; the opposite verdict raises `Contradiction` (`409`).
3. A rejection is one compare-and-set from `pending_approval` (`postgres/actions.py::decide`).
4. An approval reads the evidence again through the kind's row in `actions/registry.py::EFFECTS`, found
   with `registry.effect_for` (for a refund: `refund.changed_evidence_fields` reads the charge). Unreadable raises
   `EvidenceUnreadable` (`503`, the action stays pending); changed marks the action `stale`; unchanged is a
   compare-and-set to `approved`. A kind with no row raises `EffectUnregistered` and a row with no
   `changed_evidence_fields` raises `EvidenceCheckMissing`: both are `Internal` errors, and the action stays pending.
5. `backend/application/actions/execute.py::drive` reads the policy row and the kind's effect
   (`EffectUnregistered` before any attempt when there is none), claims one attempt
   (`postgres/actions.py::claim`), calls the effect's `perform` outside any transaction, and records the
   outcome with `record_outcome`. A retryable `ProviderError` releases the attempt, waits and goes round
   again; when no attempt can be claimed, `park` moves an action whose attempts are spent to the policy
   row's `parked` status.
6. The route answers `200` with the action and its events.

`POST /actions/{action_id}/resolution` follows the same shape through `actions/resolve.py::record`.

## Start-up

`main.py::create_app` loads the settings, configures the logger, builds the FastAPI application
(`backend/api/app.py::build`) and puts the wiring on `app.state`: `llm`, `clock`, `prompt`,
`action_timing`, and inside the lifespan `pool` and `providers`. The lifespan then applies `schema.sql`,
seeds the knowledge base, loads the sample data (below), cleans up turns a dead process left running
(`triage_turn.clean_up_at_start_up`) and drives the unfinished actions of every kind a person approves
(`execute.recover_unfinished`).

`create_app(llm, providers, clock, settings, load_sample_data, prompt)` is the only place a model, the providers, the
clock, the sample data and a prompt other than the shipped one enter.
Tests and the evaluation pass the scripted model, mock providers they can set faults on and a fixed clock;
the served application passes nothing and gets the OpenAI adapter, the mocks and the system clock.

Sample data is loaded in two cases only. When no providers are passed, `create_app` builds the mocks and
fills them with `backend/infrastructure/providers/sample_data.py::load_seed`. When a loader is passed, it is
called with the providers in use. A caller that passes its own providers and no loader gets none: the test
fixture passes `load_seed` beside its mocks, and the evaluation passes no loader and loads one scenario per
case.

## Where state lives

Everything that must survive a restart is in one PostgreSQL database, defined by
`backend/infrastructure/postgres/schema.sql` and applied at start-up without migrations.

| Table | Written by | Holds |
|---|---|---|
| `conversations` | `postgres/conversations.py`, `postgres/turns.py` | one row per ticket; `busy_turn` and `busy_until` are the per-conversation lease |
| `messages` | `postgres/conversations.py`, `postgres/turns.py` | customer, support-agent, operator and assistant messages, unique per `(conversation_id, message_key)` |
| `turns`, `steps` | `postgres/turns.py` | each unit of processing and every model call and tool call inside it |
| `decisions` | `postgres/turns.py` | append-only, numbered per conversation; the body is the decision as JSON |
| `actions`, `action_events` | `postgres/actions.py` | every proposed effect, its status, and an append-only event per status change and attempt |
| `kb_articles` | `postgres/kb.py` | the knowledge base with a generated search vector |
| `mock_fixtures`, `mock_charges`, `mock_incidents` | `postgres/billing_fixtures.py`, `status_page_fixtures.py`, `incident_fixtures.py` | the mock providers' own state |

In process memory there is only wiring and per-request context: the objects on `app.state`, the ids bound to
the logger in a `ContextVar`, and the fault queues of the mock providers (`MockBilling.faults`,
`MockIncidents.faults`), which only tests and the evaluation fill. Leases (`busy_until`, `claimed_at`) run on
the database clock. No transaction is held open across a model call or a provider call.
