# Adding a feature

Recipes for the changes this layout was built to absorb. Each names the files to touch and the tests to add
or update, and each ends with what it does **not** cover. Read `architecture.md` first for the layer rules;
`tests/architecture/test_import_rule.py` fails when a recipe is followed in the wrong package.

Run the suite after every recipe:

```sh
docker compose run --rm app pytest -q
```

It fails when a test fails and when coverage of `backend/` and `main.py` falls below 95%, so a recipe is not
done until its new code is tested. Each recipe names the folder for its tests; the rule behind the choice is
in `testing.md`: `tests/unit` for a function called directly, `tests/integration` for anything on the
database or through the in-process client, `tests/architecture` for a rule about the source, `tests/e2e`
only for what the real server process alone can show.

## A new read tool

Example: a tool that reads the customer's recent logins.

1. **One module** `backend/application/tools/<tool_name>.py`, shaped like `status_check.py` or `kb_search.py`:
   - an arguments model — `NoArguments`, or a Pydantic class with `ConfigDict(extra="forbid")`;
   - a handler `(arguments, context: ToolContext) -> dict` that returns the result with its `source` and
     `observed_at`, and raises `ToolError` for anything the model should read as a failure
     (`ToolError.from_provider(error)` for a `ProviderError`);
   - one `Tool(name=..., description=..., arguments=..., handler=...)` constant. The description is what
     the model reads: the prompt text that is sent names no tool (only the `<!-- Why: ... -->` comments of
     `triage_v3.md` do, and they are stripped before sending).
2. **One registry entry** in `backend/application/tools/registry.py`: the import, and the constant in the tuple
   the `REGISTRY` dictionary is built from. The loop sends every registered tool to the model.
3. **Where the data comes from.** An existing provider is on `context.providers`. Data in this service's own
   database gets a function under `backend/infrastructure/postgres/` and is read through `context.pool`, as
   `kb_search` does with `kb.search`. A handler never takes a customer id from the model: it uses
   `context.customer_id`.

Tests:

- Update the exact tool list in `tests/integration/test_ticket_slice.py`
  (`test_the_model_reads_the_thread_as_data_and_the_tool_result_on_the_next_step`).
- Add `tests/integration/test_<tool_name>.py` like `tests/integration/test_status_check.py`: script the model with
  `call("<tool_name>")` then `answer(DRAFT)` and assert on the recorded step. Give each `ToolError` the
  handler raises a test of its own, as `tests/integration/test_billing_lookup.py` does for an unknown account
  and a provider that does not answer.
- To require the tool in the evaluation, add its name to `required_tools` of a case in `evals/cases.py`;
  `tests/unit/test_evals.py` already asserts that every required tool is registered (for the twelve prefix cases).
- A tool whose result lists `articles` the decision may cite sets `has_citable_articles=True`, as
  `kb_search` does, and is added to the exact list in `tests/unit/test_cited_articles.py`
  (`test_only_the_search_tool_returns_articles_a_decision_may_cite`). That list is pinned on purpose: the
  flag is what lets a cited id through, so setting it is a change a test must be told about.

Nothing else changes. The registry answers two questions for the domain, and the loop passes both answers
on: `read_tool_names()` — every tool whose `is_effect` is false, which rule R4 weighs — and
`citable_tool_names()`, from which `backend/domain/decision.py::from_draft` keeps a cited id only when such a
tool returned it in this turn. The decision's `tools_called` is built from the recorded steps.

Not covered: a tool that needs a provider that does not exist yet also needs a new port — see "A new
provider" below.

## A new effect kind

Example: voiding an authorized hold. An effect is anything that changes something outside the service.

1. **Policy row** in `backend/domain/policy.py::POLICY`: who approves (`PERSON` or `"policy:<kind>"`), whether
   the key stays taken after success and rejection (`is_key_held`), where an action is left when its attempts
   are spent (`parked`), and how many one turn may propose (`per_turn`). A kind without a row gets
   `UNLISTED`: a person approves.
2. **Key identity** in `backend/domain/effect_keys.py`: one function that builds the key from the object acted
   on (`refund:{charge_id}`), never from the request, the turn or the model's call. If the provider takes
   its own deduplication key, add that function here too.
3. **Effect module** `backend/application/actions/<kind>.py`, shaped like `refund.py`:
   - `KIND = "<kind>"`;
   - a `<Kind>Denied(Denied)` error with its own class name and kebab-case `code`;
   - `propose(...)`: read the object at the provider, deny what must be denied, then call
     `store.propose(...)` with `status=action_status.PENDING` (a person approves), or with
     `status=action_status.APPROVED` and `approver=policy.approver` (the policy row approves, as
     `incident.open` does);
   - `perform(action, provider, timeout_s) -> (status, provider_ref, detail)`: one attempt. Re-raise a
     retryable `ProviderError` to mean "outcome unknown"; return `action_status.FAILED` for a refusal;
   - `changed_evidence_fields(action, provider, timeout_s) -> list[str]` when a person approves: the evidence
     fields that differ from the snapshot, or an empty list. It is required for such a kind.
4. **One registry line** in `backend/application/actions/registry.py::EFFECTS`: an `Effect` with `perform`, and
   with `changed_evidence_fields` for a kind a person approves. `tests/unit/test_effect_registry.py` fails while a policy
   row has no effect, an effect has no policy row, or a kind a person approves cannot read its evidence again.
   At run time the same two gaps fail closed: an approval of a kind without `changed_evidence_fields` raises
   `EvidenceCheckMissing` and the action stays pending, and a kind with no row raises `EffectUnregistered`
   in the approval and in the executor.
5. **The tool the model proposes with**: `backend/application/tools/<verb>_<kind>.py` with `is_effect=True`, and
   its entry in the tool registry. `is_effect=True` is what makes the loop re-check the conversation's lease
   before the call.
6. **Provider method.** If no existing port has the call, add it to the protocol in `backend/domain/ports.py`
   and to the mock under `backend/infrastructure/providers/`.

Tests, following the existing ones:

- the policy row, like `test_an_incident_is_approved_by_its_policy_row_once_per_turn_and_does_not_keep_its_key`
  in `tests/unit/test_incident.py`;
- `perform` on its own with a stub provider — a refusal fails the action, an unknown outcome is passed on —
  also in `tests/unit`, like `test_an_incident_the_incident_system_refuses_is_failed_with_the_refusal_as_its_reason`;
- the invariants with a model scripted to misbehave, like `tests/integration/test_refund_invariants.py`;
- each break point with mock faults, like `tests/integration/test_it_breaks_here.py`;
- two connections racing on the insert, if the key rule differs from a refund's, like
  `tests/integration/test_refund_races.py`;
- the tool list in `tests/integration/test_ticket_slice.py`.

`tests/unit/test_effect_registry.py` needs no change: it compares the policy table with the registry and names
only the two kinds the service ships.

What a new kind gets without any change: the state machine (`backend/domain/action_status.py`), the executor with
its claim, retries and parking (`actions/execute.py`), the approval and resolution routes, every SQL
statement in `postgres/actions.py` (`actions.kind` is free text; the live-key index reads `hold_key` from
the row), rule R1 (any unfinished action sends the ticket to a person), the `actions` list on the decision,
and what happens after a crash: start-up drives again every unfinished action of a kind a person approves
(`execute.recover_unfinished`), and a dead turn fails the unfinished actions of every kind its policy row
approves (`postgres/actions.py::fail_unfinished`). Both read the policy table.

What a new kind does **not** get, because these places name a kind today:

- Rule R2 and `agent_loop._RunningTurn.decide` read incidents only: R2 is a rule about incidents. A kind
  that needs a rule of its own adds one to `RULES` in `backend/domain/rules.py`.
- The `per_turn` limit is enforced inside `incident.open`; a new effect module enforces its own with
  `store.proposed_in_turn`.

A new action event type needs a change to the `CHECK` on `action_events.type` in `schema.sql`, and the
schema has no migrations: see "Reset" in the root `README.md`.

## A new provider, or a real one in place of a mock

The application sees providers only as the protocols in `backend/domain/ports.py` (`Billing`, `StatusPage`,
`Incidents`) bundled in `Providers`.

- **Another implementation of an existing port**: one module under `backend/infrastructure/providers/` whose
  class has the protocol's methods and raises `ProviderError(error_type, message, is_retryable)` for every failure.
  `is_retryable=True` means the outcome is unknown and the executor will call again with the same key.
  Wire it in `backend/infrastructure/providers/__init__.py`, or pass a `Providers` to `create_app`.
- **A new port**: add the protocol and a field on `Providers` in `backend/domain/ports.py`, the implementation
  under `backend/infrastructure/providers/`, and the field in `mock_providers`. Tools reach it as
  `context.providers.<name>`.
- **Library ownership**: an implementation that talks to a real system through a client library adds that
  library to `OWNERS` in `tests/architecture/test_import_rule.py` with `backend.infrastructure.providers` as its owner;
  until then the test refuses the import. The standard library's own clients (`urllib.request`,
  `http.client`, ...) need no entry: they are allowed anywhere under `backend/infrastructure`.

Tests: the contract of the implementation on its own, like
`test_the_mock_refund_follows_an_idempotency_key_contract` in `tests/integration/test_refund_invariants.py`
— in `tests/integration` when the implementation keeps state in the database as the mocks do, in
`tests/unit` against a stub of its client when it talks to a real system.

Serving with a provider that is not a mock: start-up loads the sample customers only into the mocks
`create_app` builds itself. `create_app(providers=...)` with no loader starts with no sample data; to load
some, pass `load_sample_data=` a function of `(providers, now)`. The loader used for the mocks,
`backend/infrastructure/providers/sample_data.py::load_seed`, calls `add_account`, `add_charge` and `add_page`,
which exist only on the mocks.

Not covered:

- A client library is a new dependency. `requirements.txt` is pinned; ask before changing it.
- The image starts `main:create_app` with no arguments, so serving a real provider also needs a factory
  of its own beside it, and a setting that picks it.

## A new model vendor

1. **One module** under `backend/infrastructure/llm/` with a class that has the three methods of the `LLM`
   protocol: `respond(*, instructions, items, tools, output_schema, timeout_s) -> LLMReply`,
   `user_message(text)` and `tool_result(call_id, result)`. The last two build an input item in the vendor's
   own shape; the loop only appends what they and `LLMReply.carry` return and never looks inside. `respond`
   raises exactly two errors: `LLMMisconfigured` for what a person must fix, `LLMUnavailable` for
   everything else. Switch the vendor SDK's own retries off: the loop owns retries and the deadline.
2. **Wiring**: `main.py::create_app` builds `OpenAIResponsesLLM` when no model is passed in;
   `evals/run.py` and `evals/smoke.py` build it too. A setting that picks the vendor goes
   in `backend/core/config.py`.
3. **Library ownership**: add the SDK to `OWNERS` in `tests/architecture/test_import_rule.py` with
   `backend.infrastructure.llm` as its owner.

Tests, in `tests/unit`, against a stub of the SDK's client and never the network, like
`tests/unit/test_openai_adapter.py`: the request the adapter builds, a final answer and a tool call read from
the vendor's reply, an empty or truncated answer, and every vendor error mapped to one of the two errors.

Not covered:

- A new SDK is a new dependency. `requirements.txt` is pinned; ask before changing it.
- The scripted model used by the tests builds its items in the shape of the OpenAI Responses API
  (`backend/infrastructure/llm/scripted.py`), and several tests read that shape from `llm.requests`.
- The setting is `Settings.llm_api_key`, read from `OPENAI_API_KEY`. A second vendor needs its own
  environment key and a setting that picks the vendor.

## A new route

1. **One module** `backend/api/routes/<resource>.py` with `router = APIRouter()`, the request models as Pydantic
   classes in the same module, and the text for the OpenAPI document as `description=` on the decorator.
2. **One entry** in the tuple of `backend/api/routes/__init__.py`.
3. **The work is an application function.** A route may not import `backend.infrastructure`; put the function
   under `backend/application/` (the smallest example is `backend/application/health.py`). The route reads its
   wiring from `request.app.state`; `backend/api/runner.py::build_runner` builds the turn runner from it.
4. **Errors are raised, not built.** Raise `NotFound("<what>")` when the application returns `None`; let
   every other `AppError` pass. `backend/api/problems.py` turns it into the response.

Tests, in `tests/integration`: status codes and bodies through the `client` fixture; a refusal with its
problem type, like `tests/integration/test_problem_json.py`. A route needs a test in `tests/e2e` only when
its answer depends on the server process — a header uvicorn sets, a streamed body, behaviour across a
restart; `tests/e2e/test_ticket_over_http.py` is the pattern.

## A new intake channel

Example: tickets that arrive from an e-mail webhook instead of `POST /tickets`.

1. **One route module**, as in "A new route", that maps the channel's payload to what
   `backend/application/triage_turn.py::ingest` takes: a `ticket_id` or `None`, the customer record as one
   dictionary with its `id` (or `None`), and the messages as dictionaries with `id`, `author`, `body` and
   `sent_at`. The split of the customer id from the rest of the record, the stored-decision replay and the
   turn are behind that one call.
2. **Validation is the channel's.** `ingest` checks nothing about the thread; `POST /tickets` does it with
   its request models (`TicketIn`, `MessageIn`), and a new channel must do the same before it calls: at
   least one message; every `author` either `customer` or `support_agent` (the table's `CHECK` also accepts
   `operator` and `assistant`, which ingest must never store); a non-empty `body`; a `sent_at` that carries
   a time zone.
3. **The answer** is the channel's own: `Outcome.is_created` and `Outcome.is_replayed` say what happened, and
   the route decides how to tell its caller (`POST /tickets` answers `201` or `200` and sets
   `Idempotent-Replayed`).

Tests, in `tests/integration`: the route through the `client` fixture, and a replay of the same payload, like
`tests/integration/test_replay.py`.

Not covered: a message's `author` is `customer` or `support_agent` (`THREAD_AUTHORS` in
`backend/application/model_input.py`, and the `CHECK` on `messages.author`); the conversation does not record which
channel a ticket came from.

## A new decision field

There are two kinds of field (see `design.md` §7).

**A field the model writes:**

1. Add it to `Draft` in `backend/domain/draft.py`. Every field is required; optional means `| None`; a closed
   list is a `Literal`. The strict schema sent to the model is derived from the class.
2. Add the same key to the dictionary in `backend/domain/decision.py::fallback`.
   `decision.changed_fields` walks every `Draft` field of both the new and the previous decision.
3. Describe the field in the prompt's output contract. That is a change in meaning, so it is a new prompt
   version (next recipe).
4. Update every scripted draft in the tests (`DRAFT` and `ANSWERED` in `tests/unit/test_rules.py` and the drafts built
   from them): `Draft` forbids missing and extra fields. A rule that reads the new field is tested in
   `tests/unit`, like `tests/unit/test_rules.py`.

**A field the service sets:** add it in `decision._fields_recorded_by_service`, or in
`_RunningTurn.complete` if it depends on the previous decision as `changed_fields` does.

To grade the field in the evaluation, add it to `FIELDS` in `evals/score.py` and its labels to `Case` in
`evals/cases.py`.

Not covered: decisions already stored. A decision is stored as its JSON body, and `changed_fields` reads
`previous[name]` for every draft field, so the next turn of a conversation whose last decision predates the
field raises `KeyError`. Rebuild the database (README, "Reset"), or make that read tolerant first.

## A new prompt version, and how it is measured

1. Draft the file: copy the shipped file under `backend/application/prompts/` to the next number and edit
   the copy. `triage_v3.md` exists as a version that was measured and not shipped (`eval-baseline.md`,
   "Evening passes on the final code"), so the next draft is `triage_v4.md`, copied from `triage_v2.md`. Keep a
   `<!-- Why: ... -->` comment beside each section; comments are stripped before the prompt is sent. Add
   the new stem to `tests/unit/test_prompt_files.py`, which loads every prompt file, checks that no comment
   is sent, and pins the hash of each earlier version. The served application still sends the old file.
2. Measure the draft, and the shipped version beside it, with `--prompt` (below), and record the numbers in
   `eval-baseline.md`.
3. Only then point `PROMPT_FILE` in `backend/application/model_input.py` at the new file, and
4. update the three assertions that name the shipped version: `prompt_version.startswith("triage_v2@")` in
   `tests/integration/test_ticket_slice.py`, `test_the_shipped_prompt_is_the_second_version` in
   `tests/unit/test_prompt_files.py`, and the header pattern `prompt triage_v2@` in
   `tests/integration/test_evals.py`.

Every decision stores `prompt_version` as `<file stem>@<first 8 hex characters of the SHA-256 of the sent
text>`, so an edit made in place also shows up as a new version; a new file is for a change in meaning.

Measuring is done by whoever holds the key, because it calls the real model:

```sh
OPENAI_API_KEY=... docker compose run --rm app python -m evals.run --prompt triage_v4
OPENAI_API_KEY=... docker compose run --rm app python -m evals.run --prompt triage_v2
```

`--prompt` takes the stem of any committed file under `backend/application/prompts/`; without it the
evaluation runs the file `PROMPT_FILE` names. It runs sixteen cases three times each and prints a header
line — prompt version, model requested and returned, runs, UTC start — then pass / miss / violation for
urgency and next action, forbidden effects, required tools not called, whether runs differed, the effects
the model proposed per kind and the input tokens of the first model call. A forbidden value or a forbidden
effect makes the command exit with 1. Keep each output as a file under `eval-logs/experiments/`, add the
header line and the printed table to `eval-baseline.md` as a new column beside the earlier versions and say
what moved. Two passes of the same text there differ by two runs in urgency and one in next action, and
runs of one pass disagree in eight to ten of sixteen cases, so a difference of that size is noise. A
version that is adopted moves its file to `eval-logs/shipped/`.

Not covered: a prompt text that is not a committed file cannot be named, and the header's hash is the only
record of which text a pass sent, so commit the draft before measuring it. `--prompt` changes the prompt
only: a tool description or the draft schema edited between two passes also moves the "first call in"
column, and nothing in the output says which of the three changed.

## What would require wider change

| Change | Why it does not fit a recipe |
|---|---|
| A queue or asynchronous turns | The turn runs inside the request (`design.md` §13); `triage_turn` is separate from the routes, but nothing accepts a ticket and answers later |
| A second instance | Every exclusion is a compare-and-set in PostgreSQL, but start-up recovery in `main.py` assumes nothing else is running |
| A change to a table or a `CHECK` | `schema.sql` is applied with `CREATE TABLE IF NOT EXISTS` and there are no migrations |
| A real provider in the served application | `create_app` takes the providers and the sample-data loader, but the image's factory passes neither and no setting picks a provider |
| A fake store for faster tests | Rejected on purpose: `decisions/0006-tests-on-real-postgresql-without-a-store-double.md` |
| Authentication of approvers | `approver` and `resolver` are names in the request body and are not verified |
