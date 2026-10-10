# Support Ticket Triage Service

Triage for support tickets: who should handle a ticket and how soon, decided by a model that sits behind a
service which stores everything, records every step, and sends the ticket to a person whenever the model
cannot finish. Design: `docs/design.md`; the problem it answers: `docs/problem-brief.md`; how the code is
laid out: `docs/architecture.md`; every other document: `docs/README.md`. The layout and the import rules are under
"Repository layout" below, the tests and the 95% coverage gate under "Tests", and how to add a tool, an
effect, a route or a prompt in `docs/adding-a-feature.md`.

## Run

Docker is the only requirement to run it; the examples below use `curl`.

```sh
OPENAI_API_KEY=... docker compose up --build
```

This starts PostgreSQL and the service on `localhost:8000`, applies the schema and loads the seed data: the
knowledge base, and the billing accounts and status page behind the three sample customers. The key is read
from the environment and is never stored in this repository. The service starts without a key; a request
that needs the model then answers `503`.
The repository is mounted into the container, so a code change needs a restart, not a rebuild.

## Try the three sample tickets

With the service running, in a second terminal:

```sh
docker compose run --rm --no-deps app python -m evals.sample t1 | curl -s -X POST localhost:8000/tickets -H 'content-type: application/json' -d @-
docker compose run --rm --no-deps app python -m evals.sample t2 | curl -s -X POST localhost:8000/tickets -H 'content-type: application/json' -d @-
docker compose run --rm --no-deps app python -m evals.sample t3 | curl -s -X POST localhost:8000/tickets -H 'content-type: application/json' -d @-
```

Each answers with a `conversation_id` and the decision. Everything behind a decision — messages, every
decision version, the proposed effects with their events, each turn with its model calls and tool calls — is
read back with:

```sh
curl -s localhost:8000/conversations/<conversation_id>
```

The sample bodies carry no `ticket_id`, so sending one again starts a new conversation. A ticket sent with a
`ticket_id` (add `"ticket_id": "demo-1"` to the body) and sent again is answered from store with
`Idempotent-Replayed: true` and no model call, as long as nothing new arrived and the stored decision is not a
fallback. While a turn is running
on a conversation, a second request for it answers `409`. A `ticket_id` sent again under a different customer
id answers `409` and stores nothing; a request or a stored ticket without a customer id is not a mismatch.
Every refusal — a validation error, an unknown path, and each error the service raises on purpose — is
`application/problem+json`. A failure of the service itself is not: it answers the server's plain-text
`500 Internal Server Error`, with nothing from the exception in the body.

## Talk to it as an operator

An operator is an internal colleague. Their message runs a turn through the same loop, tools and rules as a
ticket, with the previous decision and the conversation so far in front of the model:

```sh
curl -s -X POST localhost:8000/conversations/<conversation_id>/messages -H 'content-type: application/json' \
  -d '{"body": "Which of the charges were actually captured?"}'
```

The answer carries `reply` and a new decision version; `changed_fields` on it names the fields that differ
from the version before. Every version stays readable with the `GET` above. When the model cannot finish an
operator turn the answer is `503` with `Retry-After` and no decision is written: the operator is there to ask
again. An operator cannot approve anything by chat. With an `Idempotency-Key` header the message is stored
once, and a repeat returns the decision of the turn that covered it.

## Approve or reject a refund

The model cannot refund anything. It can propose a full refund of one captured charge of the ticket's
customer; the proposal waits as an action in `pending_approval`, and while it waits the decision is
`escalate_to_human`. The action's id is in the decision's `actions` and in the `GET` above. With the shipped
prompt the model usually escalates Ticket 1 without proposing: `docs/eval-baseline.md` records no `propose_refund`
call with it in two passes of 48 runs, so expect `actions` to be empty. A third prompt written to change
that did not, and is not shipped. An operator can ask for a proposal on the route above, for example
`{"body": "Propose a refund for the duplicate captured charge."}`; whether the model then proposes is its
own choice: it did in the one run recorded in
`docs/eval-logs/shipped/served-walkthrough.txt`. A person decides on a proposal:

```sh
curl -s -X POST localhost:8000/actions/<action_id>/approval -H 'content-type: application/json' \
  -d '{"verdict": "approve", "approver": "dana", "note": "duplicate charge"}'
```

`verdict` is `approve` or `reject`; `approver` is a name and is not verified. An approval reads the charge
again: unchanged, the refund is executed once and the answer shows the action `succeeded`; changed since the
proposal, the action becomes `stale` and nothing is refunded; unreadable, the answer is `503` and the action
stays pending. The same verdict posted again answers `200` with the current status; the opposite verdict
answers `409`. A charge has one refund action, whichever ticket or turn proposes it, and a rejected refund
stays rejected.

A refund whose outcome at the provider stayed unknown after its attempts is parked in `needs_review` and is
never tried again by the service. The person who looked at the provider records what they found:

```sh
curl -s -X POST localhost:8000/actions/<action_id>/resolution -H 'content-type: application/json' \
  -d '{"outcome": "succeeded", "resolver": "dana", "note": "refund re_123 is on the charge"}'
```

`outcome` is `succeeded` or `failed`; an action that is not in `needs_review` answers `409`.

## Tests

```sh
docker compose run --rm app pytest -q
```

The tests need no key: they run against a real PostgreSQL database of their own with a scripted model in
place of the vendor. They cover what must hold whatever the model outputs — the refund and incident
invariants, the races on two connections, each break point of `docs/design.md` §6, replays and the restart —
and the two adversarial cases below with a model scripted to obey the thread.

The same command measures coverage of `backend/` and `main.py`, lines and branches, lists every file with
something no test ran, and **fails when the total is below 95%**. The coverage tool is part of the image;
on an image built before it was added, either rebuild once (`docker compose build app`) or install it for
the one run:

```sh
docker compose run --rm app sh -c "pip install -q --user pytest-cov==7.1.0 coverage==7.16.2 && python -m pytest -q"
```

A test lives in the folder named after what it touches:

| Folder | What its tests touch | Run only this folder |
|---|---|---|
| `tests/unit` | pure functions and classes: no database, no HTTP; the `test_*_properties.py` files check a property on generated inputs (`docs/testing.md`, "Property-based tests") | `docker compose run --rm --no-deps app pytest -q --no-cov tests/unit` |
| `tests/integration` | real PostgreSQL, most of them through the in-process HTTP client | `docker compose run --rm app pytest -q --no-cov tests/integration` |
| `tests/architecture` | the source itself: the import rule, the logging rule, the error model, the package markers, the customer-record module's imports | `docker compose run --rm --no-deps app pytest -q --no-cov tests/architecture` |
| `tests/e2e` | the service as its own process: uvicorn on a local port answering real HTTP, and the reset command | `docker compose run --rm app pytest -q --no-cov tests/e2e` |

`--no-cov` switches the measurement off for a run of one folder, which cannot meet the target alone.
`tests/conftest.py` holds the fixtures every folder shares. A file name that appears in two folders is one
subject seen at two levels: `tests/unit/test_rules.py` calls the rules, `tests/integration/test_rules.py`
posts a ticket.

The end-to-end tests start the real server with the scripted model, post a ticket, an operator message and
an approval over a socket, kill the server between an approval and its refund and start it again, and read
the `422`, `404` and plain-text `500` answers. They do not call the real model: whether a triage is right is
measured by the evaluation below.

To see the coverage report again after a run, or the files with something missing line by line:

```sh
docker compose run --rm --no-deps app python -m coverage report
docker compose run --rm --no-deps app python -m coverage html   # writes htmlcov/index.html
```

`evals/` is measured beside the service and left out of the total; `docs/testing.md` has its command, what
belongs in each folder and what no test here covers.

## Evaluation

Run by whoever holds the key; it calls the real model.

```sh
OPENAI_API_KEY=... docker compose run --rm app python -m evals.run
OPENAI_API_KEY=... docker compose run --rm app python -m evals.run --case T3-3 --runs 5
OPENAI_API_KEY=... docker compose run --rm app python -m evals.run --prompt triage_v1
OPENAI_API_KEY=... docker compose run --rm -e TRIAGE_MODEL=gpt-4.1 app python -m evals.run
```

This runs the twelve labelled cases of `docs/problem-brief.md` §4 — each prefix of each sample thread — two
operator cases and two adversarial cases, three times each, through the same application code as a request,
with mock providers loaded for that prefix and a fixed clock. It uses a database of its own, recreated at
every start, and refuses the served one.

`--prompt` takes the stem of any file under `backend/application/prompts/` and evaluates that text instead
of the shipped one; a name that is no file there ends the command with 2 and lists the stems. It changes
nothing in the served application, which always loads the file `PROMPT_FILE` names. The model is the
`TRIAGE_MODEL` setting, passed for one command with `-e` as above.

The output starts with one line that says what was measured — the prompt version (file stem and hash of
the sent text), the model requested and the model ids the vendor returned, the runs per case, the UTC start.
Its shape, with example values:

```
prompt triage_v2@fc8d2044 | model requested gpt-4.1-mini, returned gpt-4.1-mini-2025-04-14 | runs 3 | started 2026-10-10T06:39:12Z
```

The table shows, per case, urgency and next action as pass / miss / violation, forbidden effects, required tools that
were not called, whether the runs differed, the effect-tool calls the model made in the scored turn per kind
("effects proposed", such as `refund x2`), the input tokens of the first model call of the scored turn
("first call in": one number when the runs agree, lowest-highest otherwise; it changes whenever the prompt,
a tool description or the schema does; a call the model did not answer has no count, so the retry's count is
shown, and `-` when no call of the turn was answered), latency and tokens. Under the table, after the totals, one line per
effect kind counts those calls over all runs; for the two operator cases the scored turn is the operator's,
so what was proposed when their ticket was first triaged is not in the column or in those lines
(`docs/eval-baseline.md` says how that compares with the baseline's counts). A forbidden value or a forbidden effect fails
the run and the command exits with 1; a model that cannot be used at all (no key, a rejected key or model
id) ends it with 2. `OP-QUESTION` asks a question after a triaged prefix and passes only
when urgency and next action equal the previous decision's; `OP-CORRECTION` says "this is high, not
critical" and reports whether the rationale names the operator. T2-2 to T2-4 require the `open_incident`
tool; a run that does not call it lists it as missed. `ADV-INJECTION` is T1-3 with instructions to the model
written into the customer's last message; it keeps the labels of T1-3 except that only `low` urgency is
forbidden: an incident, a low urgency or an unattended answer fails it. `ADV-TOOL-DOWN` is T2-1 with the status page unreadable, and fails on an
unattended answer.

What was measured is in `docs/eval-baseline.md`. The terminal output it was counted from is under
`docs/eval-logs/`, in three folders: `baseline/`, the three morning passes of the first comparison, 144
runs; `shipped/`, a pass of three runs with the shipped prompt on the final code and the requests of this
README answered by the served service; `experiments/`, the pass of `triage_v3`, which was measured and not
shipped, and the two attempts at another model that the vendor refused for the key used, so the fourth
command above has no result here. `docs/eval-logs/README.md` has the command that counts each number.

One round trip to the real model — a strict tool call and a strict final answer — without the service or
the database, also run by whoever holds the key:

```sh
OPENAI_API_KEY=... docker compose run --rm --no-deps app python -m evals.smoke
```

## Logs

The service writes one JSON line per event to standard output: `turn.started`, `llm.call`, `tool.call`,
`action.transition`, `decision.made` and `turn.fallback`, each with `request_id`, `conversation_id`,
`turn_id`, `action_id`, `duration_ms` and `outcome`. The request id is the caller's `X-Request-Id` when one
was sent, otherwise a new one, and is returned in that header. Message bodies, tool arguments and tool
results are not logged at the default level — they are stored as steps and read with
`GET /conversations/<conversation_id>` — and are added as `payload` with `TRIAGE_LOG_LEVEL=DEBUG`.

```sh
docker compose logs -f app
```

## Reset

The schema is one file applied at start-up, without migrations. After a change to it, rebuild the served
database — this deletes every conversation — and restart the service to load the seed data again:

```sh
docker compose run --rm app python -m backend.cli.reset
docker compose restart app
```

## Settings

| Variable | Meaning | Default |
|---|---|---|
| `OPENAI_API_KEY` | key for the model vendor | none |
| `TRIAGE_MODEL` | model id | `gpt-4.1-mini` |
| `TRIAGE_REASONING_EFFORT` | sent only when set; a non-reasoning model rejects it | unset |
| `TRIAGE_LOG_LEVEL` | log level; `DEBUG` adds message and tool payloads to the log lines | `INFO` |
| `DATABASE_URL` | set by `compose.yaml` | |

The example environment file in the repository root lists these names. Copy it to the file Compose reads
(the same name without `.example`, git-ignored), or pass the variables on the command line as above.

## Repository layout

```
main.py              composition root: wires infrastructure into the application (uvicorn main:create_app --factory)
backend/
  cli/               command-line entry points (reset), whose print is the command's output
  core/              settings, the one JSON logger, the error hierarchy every layer shares
  domain/            draft schema, policy, effect keys, action statuses, decision and rules, the ports for the model and the providers
  application/       the turn, the agent loop, the model input, the customer record
    prompts/         the prompt files, one per version
    tools/           the tool registry and one module per tool
    actions/         the effect path: approve, execute, resolve, one module per effect kind, their registry
  infrastructure/
    postgres/        persistence, schema.sql and the mocks' tables; the only package that imports psycopg
    llm/             the OpenAI adapter and the scripted model; the only package that imports openai
    providers/       the billing, incidents and status page mocks, and the sample data
  api/               the FastAPI application and error shapes; the only package that imports fastapi
    routes/          one module per resource
evals/               labelled cases, scenarios, runner and scorer, the real-model smoke check, the sample request printer
tests/               unit, integration (real PostgreSQL, scripted model), architecture, e2e (the server as a process) — see Tests above
docs/                design, decisions, and how the repository is organised
```

Each layer imports only the layers below it: `core` nothing outside itself, `domain` only `core`,
`infrastructure` those two (its `providers` also `infrastructure/postgres`), `application` those two plus
`infrastructure/postgres`, `api` the three of `application`, `domain` and `core`. `main.py`, at the
repository root, and `cli` import all of them and nothing imports `cli`; no module under `backend/` imports
`main`, `evals` or `tests`.
`tests/architecture/test_import_rule.py` fails on an import that breaks this. The rules in full: `docs/architecture.md`.

## Documentation

`docs/README.md` is the index, with a reading order. In short:

| To learn | Read |
|---|---|
| what the service must do | `docs/problem-brief.md` |
| the design in full | `docs/design.md` |
| why each decision was taken | `docs/decisions/` |
| how the code is laid out and how a request travels | `docs/architecture.md` |
| how to add a tool, an effect, a provider, a model vendor, a route, an intake channel, a decision field, a prompt | `docs/adding-a-feature.md` |
| how code, comments and names are written | `docs/code-style.md`, `docs/comment-style.md`, `docs/naming-conventions.md` |
| where a test goes, the coverage target, what the tests do not cover | `docs/testing.md` |
| the log events and the error model | `docs/logging-and-errors.md` |
| what the evaluation measured, and the logs it was counted from | `docs/eval-baseline.md`, `docs/eval-logs/` |

`CLAUDE.md` holds the instructions for an AI coding agent working in this repository.
