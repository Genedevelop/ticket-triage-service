# System design — Support Ticket Triage Service

Status: approved by the owner and built, except what §12 marks as not built. Revision 3, after two paper reviews by independent readers.
This answers `problem-brief.md` (cited as Brief §n); `L40` cites a line of the assignment. `PROBE` marks
something assumed here and checked against the real API before code depends on it. `MEASURED` marks a
database claim that was run with two racing sessions on a disposable PostgreSQL 16 before this was written
(the service and its tests now run PostgreSQL 18, where the integration tests prove the same statements).

## 0. The whole thing on one page

```
sender ──POST /tickets──────────────┐
operator ──POST /conversations/{id}/messages──┤
                                    ▼
                         api  (validation, status codes, replay)
                                    │
                         turn runner (one turn at a time per conversation)
                                    │
                         agent loop ──▶ LLM port ──▶ OpenAI
                           │      │
              read tools ◀─┘      └─▶ effect tools ── propose ──┐
        (billing, status, KB)                                   ▼
                                                        ACTIONS  (key → policy → state machine)
approver ──POST /actions/{id}/approval─────────────────────────▶│
                                                                ▼
                                                    providers (refund, incident)

everything above reads and writes one Postgres database; nothing is kept in process memory
```

Seven decisions carry the design. Each names what it rejects.

| # | Decision | Rejected | Why |
|---|---|---|---|
| D1 | The model proposes an effect; a policy table says who approves it; code executes it. One path for every effect. | Giving the model a tool that performs the refund | Brief §6. The model has no tool that reaches a money-moving provider, so no model output can move money. |
| D2 | An effect is identified by the thing it acts on (the charge, the open fault), never by the request, the turn or the model's call. | A key per request or per tool call | Brief §7. The model re-decides on every turn; a per-call key would be fresh each time. The one model-chosen part of any identity is an incident's `component`, from a list of four; its worst case is in §13. |
| D3 | Each turn rebuilds its context from our database. | Provider-side conversation state; an in-process session | Must survive a restart (`L39`), be auditable (`L38`), and not depend on what the vendor stores. |
| D4 | A hand-written loop: one module, `application/agent_loop.py`, of about 250 lines. | LangGraph, OpenAI Agents SDK, Pydantic AI | None gives exactly-once effects. A checkpoint belongs to one run of one conversation; an approval belongs to a charge and must be found again from any conversation or turn. The control points this problem needs (check before dispatch, step audit, budgets, fallback) *are* the loop. |
| D5 | Code may move the model's decision only toward a human, never away. | Trusting the draft; free-form "fix-up" rules | Every override is safe by construction and easy to state. |
| D6 | No in-memory double of the store. Tests run on real Postgres. | A fake repository | In round 1 (an earlier attempt at this assignment) a fake diverged from Postgres and hid an ordering defect on the money path. |
| D7 | Synchronous Python: FastAPI, psycopg 3, plain SQL in one `schema.sql`, PostgreSQL. | async stack; an ORM; a migration tool; SQLite | Every guarantee here is a conditional update or a unique index that must hold between concurrent writers on one clock, and stay true with a second instance: that is what Postgres is for, and what I would run in production. Python because the model SDK and the team's tooling are there; nothing in the design depends on it. One instance and seconds-long turns make a thread per turn enough. Costs are in §13. |

## 1. Layout

```
main.py            create_app(): the composition root — wiring, start-up (schema, seed, recovery of unfinished work)
backend/
  cli/             command-line entry points (python -m backend.cli.reset); the only package that prints
  core/            shared by every layer: settings read once, JSON log lines + request/conversation/turn ids, the error hierarchy
  domain/          pure — draft schema, policy, effect keys, action statuses, decision + rules, the model and provider ports
  application/     the brain — turn runner, loop, context builder, customer record, prompts
    tools/         the tool registry and one module per tool
    actions/       the effect path — approve, execute, resolve, one module per effect kind, a registry by kind
  infrastructure/
    postgres/      persistence, including the mocks' tables — the only package that imports psycopg
    llm/           the only package that imports openai; also the scripted model used by tests
    providers/     billing, incidents, status page: the mocks' contract logic; the sample data and its loaders
  api/             transport, one route module per resource, errors to problem+json — the only package that imports fastapi
evals/             labelled cases, scenarios, runner, scorer, the real-model smoke check
tests/             unit/ (pure functions), integration/ (real PostgreSQL, scripted model), architecture/ (the structure itself), e2e/ (the server as a process)
docs/              this design, the decision records, and how the repository is organised (index: README.md)
```

Two structural rules, enforced by `tests/architecture/test_import_rule.py`. **Each external library is imported by exactly
one package** (`fastapi` and `starlette` → `api`, `psycopg` and its pool → `infrastructure/postgres`,
`openai` → `infrastructure/llm`). **Each layer imports only the layers below it**: `core` nothing outside itself,
`domain` only `core` (and, outside this repository, only `pydantic` and a fixed list of standard-library modules,
`DOMAIN_STDLIB` in the test), `infrastructure` the two of them and its own packages, `application` those two
plus `infrastructure/postgres` — the model and the providers reach it as ports from `domain/ports.py` — and
`api` the other three (`application`, `domain`, `core`); only `main.py`, at the repository root, and the commands under `cli/` import all of them,
nothing imports `cli/`, and nothing under `backend/` imports `main`, `evals/` or `tests/`. The store is used directly
because of D6: nothing would ever stand in for it. The rules in full, with the request paths:
`architecture.md`.

Two of the four other tests in `tests/architecture` keep the shared pieces single: `tests/architecture/test_error_model.py` (every exception under `backend/` is
an `AppError` of one category, and each category has one HTTP status) and `tests/architecture/test_logging_rule.py`
(only `core/logging.py` uses the standard logger; `print` only there and under `cli/`). Both are described
in `logging-and-errors.md`.

Extension points (`L126`), and where each lives:

| To add | Change |
|---|---|
| a tool | one module under `application/tools/` and one registry line |
| an effect | a key function, a row in the policy table (§6), one module under `application/actions/` and one registry line |
| a provider | one module under `infrastructure/providers/` behind a port of `domain/ports.py` |
| a route | one module under `api/routes/` and one line in its `__init__.py` |
| a next action | one row in the ranked list (§7) saying where it sits toward a human and whether a person reads the ticket first; a definition in the prompt; rows on the label sheet |
| a language | nothing in code; labelled cases |
| a model vendor | `infrastructure/llm/` |

The exact files and tests for each, and what each does not cover: `adding-a-feature.md`.

## 2. Data model

| Table | Holds | Notes |
|---|---|---|
| `conversations` | one per ticket: `ticket_id` (unique, nullable), customer id and metadata (both nullable), `busy_turn`, `busy_until` | the busy pair is the per-conversation lock (§4) |
| `messages` | customer, support-agent, operator and assistant messages | `unique (conversation_id, message_key)`; the key is the sender's id, else a hash of author, time and body; read in order of `sent_at`, then arrival |
| `turns` | one unit of processing: `kind` (ingest / operator), `status` (running / completed / failed / abandoned), the last message it covers | what a retry replays |
| `steps` | every model call and tool call inside a turn: name, arguments, result, ok, duration, tokens | the audit trail (`L38`, `L52`) |
| `decisions` | one per completed turn, append-only, numbered per conversation; model id as returned by the API, prompt version | never updated |
| `actions` | every effect proposed: `kind`, `key`, `hold_key`, payload, evidence snapshot, `status`, provider ref, `claimed_at`, `attempts` | §6 |
| `action_events` | append-only: proposed, joined, approved, rejected, stale, claimed, provider result, parked, resolved — with actor, conversation and detail | "side-effect attempts" (`L39`); also links an action to every conversation that touched it |
| `kb_articles` | a handful of documents with a generated `tsvector` | |
| `mock_charges`, `mock_incidents`, `mock_fixtures` | provider state: charges with their refund, incidents, read-only account and status-page fixtures | written on their own connection, so "the provider acted, we crashed" is a real state |

No transaction is held open across a model call or a provider call. Mutual exclusion is a compare-and-set
update on a status column, and each status change writes its event row in the same transaction.

## 3. HTTP API

| Method and path | Purpose | Success | Refusals |
|---|---|---|---|
| `POST /tickets` | ingest a thread; start or continue a conversation; triage | 201 new, 200 continued or replayed | 409 busy, 409 ticket belongs to another customer, 422, 503 model misconfigured |
| `POST /conversations/{id}/messages` | operator turn | 200 reply + decision | 404, 409 busy, 422, 503 model unavailable |
| `GET /conversations/{id}` | messages, every decision version, actions with their events, turns with their steps | 200 | 404 |
| `POST /actions/{id}/approval` | a person approves or rejects a proposed effect | 200 with the action's status | 404, 409 contradicts the recorded verdict, 422, 503 evidence unreadable |
| `POST /actions/{id}/resolution` | a person records the real outcome of an action in `needs_review` | 200 | 404, 409, 422 |
| `GET /healthz` | process up, database reachable | 200 | 503 |

Channels are physically separate: customer text enters only through `/tickets`, operator text only through
`/conversations/{id}/messages`, approval only through `/actions/{id}/approval`. "Trust is set by the channel"
(Brief §2) is a property of the routes, not of a role field someone fills in.

`POST /tickets` body: `ticket_id?`, `customer? {id?, plan?, region?, seats?, tenure_months?, prior_tickets?}`,
`messages [{id?, author: customer|support_agent, body, sent_at}]` (at least one). The samples carry no
customer id, and a ticket with no metadata must still reach a person: without a customer id the
customer-scoped tools return the structured error they return on any failure, a refund cannot be proposed,
and the rationale says what was missing.

Errors are `application/problem+json` (RFC 9457): `type`, `title`, `status`, `detail`, and `errors[]` for
validation. Every error the service raises belongs to one category, and `api/problems.py` maps each
category to its status once; the same module replaces FastAPI's default shapes for validation errors and
for its own HTTP errors. Anything unexpected — an `Internal` error or an exception that is not an
`AppError` — has no handler there: the server answers its plain-text `500 Internal Server Error`, which is
not a problem document and carries nothing from the exception.

**Replays.** Each route's identity comes from the sender (Brief §7), and both use one mechanism — the
message key:

- *A ticket* is identified by its `ticket_id`. Messages already stored are skipped. The triage is
  **complete** only when a decision that is not a fallback covers the last stored message: then the stored
  decision is returned with `Idempotent-Replayed: true` and no model call. In every other case — a new
  message, an earlier attempt that died, a fallback, a 503 — the turn runs. The conversation, its messages
  and the turn are created in one transaction.
- *An operator message* is identified by an optional `Idempotency-Key` header, used as that message's key.
  The same key again → the message is not stored twice; the decision of the turn that covered it is
  returned, or the turn runs if it never finished. Without a key, the same words are a new turn.
- "Still running" means the conversation's lease is held, and answers 409. It never depends on a status
  that only a restart would clean.

Neither is what prevents a double refund or a double page. A sender who retries blindly with no ticket id
and no key gets a second conversation and a second triage — and still one refund per charge and one page
per open fault, because effects are keyed by their object (§6).

**When the model fails.**

| What happened | Ingest | Operator turn |
|---|---|---|
| Unavailable: everything not listed below — time-out, a rate-limit 429, vendor 5xx, a 400 on this request, repeated invalid output, budget spent | ticket stored; 201 with a decision marked `source: fallback`, `next_action: escalate_to_human`, `urgency: null` and a `fallback_reason` carrying the vendor's code | 503 with `Retry-After`; no decision is written |
| Misconfigured: no key set; the vendor answers 401, 403 or 404 (key or model id); or a 429 of type `insufficient_quota` (the account has no credit) | ticket stored; 503 `model-misconfigured` naming what was rejected and carrying the conversation id; logged as an `llm.call` event with outcome `misconfigured` | the same 503 |

The LLM port raises exactly these two errors. The vendor client is built on first use, so the service
starts without a key. A ticket is never lost because a vendor is slow, and a wrong key or model id is not
hidden behind a quiet fallback: whoever runs the service sees it on the first request.

## 4. A turn

```
run_turn(conversation, kind, clock):
  claim the conversation            UPDATE … SET busy_turn, busy_until WHERE free or lease expired → else 409
  build the model input from stored state (§8); too large → fallback
  until the deadline (60 s) or 8 steps:
      reply = llm.respond(prompt, items, tools, decision schema, timeout = min(30 s, time left))
      tool calls?  validate arguments
                   effect tool → check this turn still holds the conversation
                   run the tool → record the step → append the result → continue
      otherwise    parse the draft; invalid → one corrective round with the errors, then fallback
  apply the rules (§7), assemble the decision
  release the conversation and write the decision in one statement — only the holder can (MEASURED)
```

- One deadline per turn, checked before every step. The SDK's own retries are switched off (its defaults
  are 10 minutes and 2 retries); the loop retries a failed model call once if time is left. Every provider
  method takes a time-out and the mock honours it itself, so there are no threads to cancel. Worst case a
  turn runs about 95 s; the lease is 120 s.
- A turn that lost the conversation cannot fire an effect and cannot write a decision; its request gets 409.
- A tool that fails returns a structured error *to the model* (`type`, `message`, `retryable`) and is
  recorded as a failed step. It cannot fail silently: the decision lists every tool called.
- Tool calls are sequential (`parallel_tool_calls` off): the order of effects is the order in the audit.
- A turn that dies leaves steps and possibly actions behind. Re-running it is safe because actions are found
  again by their key; the abandoned turn stays in the audit trail.
- **Cleaning up a dead turn** happens in two places and does the same three things: mark the turn
  `abandoned`; mark its unfinished incident actions `failed: outcome_unknown`; and, for an ingest turn,
  append a fallback decision (`fallback_reason: turn_abandoned`) so the ticket is in front of a person even
  if the sender never retries. It runs when a new turn claims a conversation whose lease had expired, and at
  start-up for every conversation. Start-up may ignore leases because with one instance nothing else is
  running; with several, only the first place remains.
- Leases (`busy_until`, `claimed_at`) always use the database clock, never an injected one.

**An operator turn** is the same loop with the same tools under the same policy. The operator is a colleague:
their message may make the model look something up, re-triage, or propose an effect — and the proposal goes
through §6 like any other. The prompt asks the model to keep the previous decision's fields unless the
operator's message or new evidence gives a reason, and to say what changed. The service records which
fields differ from the previous version. An operator cannot approve by chat; there is no tool for it.

`PROBE`: Responses API with `store=False`; strict function tools together with a strict JSON-schema final
answer; what must be carried between steps for a reasoning model; which generally available model id and
effort give acceptable latency. The answers change `backend/infrastructure/llm/` only.

## 5. Tools

Every tool is: a name, a description, a Pydantic argument model (turned into a strict schema), a handler
taking a context the model cannot influence (`conversation_id`, customer, turn, `now`), and two flags: whether
it is an effect, and whether its result lists articles a decision may cite.

| Tool | Arguments from the model | From context | Returns | Built-in way to mislead |
|---|---|---|---|---|
| `billing_lookup` | — | customer id | plan; last upgrade attempt and its status; every payment attempt with `charge_id`, amount, state (`authorized` / `captured` / `failed` / `refunded`), time; `observed_at` | Ticket 1's account says the upgrade **failed** and the plan is Free, while charges exist |
| `status_check` | — | — | the public status page per component and region with `page_updated_at`; separately, the incidents currently open | Ticket 2's page says **operational** and no incident is open |
| `kb_search` | `query` (English keywords) | — | up to 3 articles: id, title, excerpt, `updated_at`, score | Ticket 3's article says dark mode comes from *Settings > Appearance > System Default*, which follows the operating system — true until message 3 shows it does not |
| `propose_refund` | `charge_id`, `reason` | customer id | the action and its status, or a denial with a reason | — |
| `open_incident` | `component` (closed list), `summary` | region (`global` when unknown) | incident id and whether it already existed, or a denial | — |

- No tool takes a customer id from the model, and none takes an amount. Both come from code.
- `component` is one of `web_app`, `api`, `login`, `payments` — four coarse values on purpose, since it is
  part of what makes two reports "the same fault".
- Results name their source and their age, so "the status page said so at 14:02" is evidence the model can
  weigh against the thread (Brief §5), not a fact.
- `kb_search` is Postgres full-text search over English. The model writes the keywords; code keeps only
  alphanumeric tokens and joins them with OR (the built-in web-style parser ANDs them, which returns nothing
  for a three-word query). A query with no usable token is a tool error, not an empty result.
- Mocks follow real contracts. The refund call takes an idempotency key: same key and parameters → the same
  refund; same key, different parameters → an error; and distinct errors for "not refundable" and "already
  refunded". A charge carries its refund and the key that made it, so a reader can tell whose refund it is.
  The incident call takes a dedup key that attaches to an incident **while it is open** — it is not a retry
  key, and after resolution the same key opens a new incident.
- A mock can be told to be slow, to fail before acting, to act and then lose the response, or to answer
  with a record for a different charge or amount. Faults are attributes of the mock object set by tests and
  by the evaluation; the running service has none. A simulated crash is an exception the executor does not
  catch.

## 6. The effect path

The first of the three things done properly (Brief §9). Both hard requirements live here.

**Policy** — one table in `domain/policy.py`. This is the autonomy boundary in code (`L29`):

| What | Who approves | Keeps its key when finished | If the outcome is unknown | Why |
|---|---|---|---|---|
| `refund` | a person, for any amount | yes — after success and after a person's rejection | read the charge: a refund made with our action id for our amount is ours → succeeded; none → call again with the same key; three attempts, 1 s then 3 s apart, then `needs_review` | money cannot be taken back by us |
| `incident` | the policy; at most one per turn | no — held only while in flight | call again, three attempts, 1 s then 3 s apart, then `failed`; never again after its turn | an incident can be acknowledged and resolved; a missed page is worse than a second one |
| answering with no person reading first (`auto_respond`) | the policy, only when R3 and R4 hold (§7) | — | — | a reply cannot be taken back, so its conditions are code |
| anything not listed | a person | — | — | fail closed |

A row's approver may depend on facts that code reads from a provider — amount, charge state, how many
refunds this customer had — and never on model output or ticket text. A row that approves automatically is
a person's standing approval with a stated maximum loss, recorded as approver `policy:<row>`: the authority
is the row's author, not the model. "Refunds under $10 are automatic" is therefore a change to one row, and
Brief §6 still holds.

**States** — one machine for every kind; only the approver differs:

```
propose ─▶ pending_approval ─approve─▶ approved ─claim─▶ executing ─▶ succeeded
               │  ├─reject (a person)─▶ rejected  ▲         ├──────▶ failed        the provider refused, definitively
               │  └─evidence changed──▶ stale     │         └──────▶ needs_review  still unknown after three attempts,
policy approves ──────────────────────────────────┘                                or the answer does not match the request
                                                needs_review ─resolution─▶ succeeded | failed
```

**Identity** — three layers, each closing a different gap (Brief §7):

| Layer | Refund | Incident | Stops |
|---|---|---|---|
| Our key, unique among actions that hold it | `refund:{charge_id}` | `incident:{conversation_id}:{component}:{region}` | us doing the same thing twice: the model re-deciding, two requests, two turns, two conversations |
| What the provider is given | the action's id, as idempotency key | `{component}:{region}`, as dedup key | refund: a re-sent call after a lost answer. incident: other tickets about a fault that is still open |
| The object's own state at the provider | "already refunded", and by which key | "an incident is open for this key" | everyone else: a refund made by hand, a fault already being handled |

We own a charge's refund lifetime, so our key is never released after success. The incident system owns
open-versus-resolved, so our key is released once the call is recorded, and the same fault recurring after
resolution pages again, as it should.

```sql
CREATE UNIQUE INDEX actions_live_key ON actions (key)
 WHERE status IN ('pending_approval', 'approved', 'executing', 'needs_review')
    OR (hold_key AND status IN ('succeeded', 'rejected'));        -- hold_key is copied from the policy row
```

`MEASURED`: the index accepts this predicate; a second insert on a held key inserts nothing and raises
nothing; a concurrent inserter waits for the first and then inserts nothing, or inserts if the first rolls
back; `stale`, `failed` and a succeeded incident release the key.

Whenever a proposal finds an action that already holds its key, a `joined` event is written for the
proposing conversation in the same transaction, so R1 and the decision see that action from every
conversation that touched it. The model's stated reason is stored on the event and is never compared.

**Proposing a refund**, in this order — the order is the safety property:

1. Fetch the charge from the billing provider. Not found, or it belongs to another customer → the same
   denial for both: the model learns nothing about other customers' charges or about our records of them.
2. State must be `captured`. A hold is denied with "needs a void, which this service does not do";
   `refunded` and `failed` are denied as such.
3. Amount and currency are copied from the charge. Snapshot the evidence, including the owner.
4. Insert, or get the action that holds the key — including one a person rejected, which is returned as a
   denial with their name and note. The model is told the action's id and status. Nothing is executed.

**Approving**, in this order:

1. Read the action's status first. The same verdict posted again on an action already approved, executing
   or finished returns 200 with its current status, and drives it if it is unfinished. The opposite verdict
   returns 409.
2. A rejection needs no evidence: compare-and-set `pending_approval → rejected`, recording who and why.
3. An approval fetches the charge again. Unreadable → 503, the action stays pending. Changed since the
   snapshot → `stale`. Unchanged → `approved`, recording the approver. Both are compare-and-set from
   `pending_approval`. Two approvers racing: one update matches, the other reads the result (`MEASURED`).

**Executing** — the same driver for every kind:

```sql
UPDATE actions SET status = 'executing', claimed_at = now(), attempts = attempts + 1
 WHERE id = $1 AND attempts < $max
   AND (status = 'approved'
        OR (status = 'executing' AND (claimed_at IS NULL OR claimed_at < now() - $lease)))
RETURNING *;
```

Commit; call the provider outside any transaction with a 10 s time-out; record the outcome with
`WHERE id = $1 AND status = 'executing' AND attempts = $n`, so an attempt that is no longer current cannot
write. An unknown outcome (time-out, 5xx, rate limit, "key in use") clears `claimed_at`; the same driver
waits 1 s, then 3 s, and goes round again. The lease is 60 s. When the claim matches nothing, one more
statement parks a row whose attempts are spent **and whose lease is free** — so an attempt still in flight
is never parked under itself:

```sql
UPDATE actions SET status = $parked          -- needs_review for a refund, failed for an incident
 WHERE id = $1 AND status = 'executing' AND attempts >= $max
   AND (claimed_at IS NULL OR claimed_at < now() - $lease);
```

`MEASURED`: one claimer per lease; the loser waits and matches nothing; a released lease is re-claimed
immediately; a late outcome write matches nothing; the parking statement fires at three attempts.

| It breaks here | State left behind | What happens next |
|---|---|---|
| a refund, before the claim commits | `approved` | recovery claims it |
| a refund, after the claim, before the call | `executing` | recovery claims it; called once |
| after the provider refunded, before we recorded | `executing` | recovery reads the charge, finds a refund made with our action id and amount, records `succeeded` without calling — if an attempt remains; otherwise `needs_review` |
| the provider times out | `executing`, lease cleared | same driver, same key, after 1 s and 3 s |
| three attempts without an answer | `needs_review` | a person checks the provider and posts the resolution |
| the answer names another charge or amount | `needs_review` | same |
| the charge was refunded by someone else | `failed: already_refunded` | nothing to do; the charge shows whose refund it was |
| an incident call fails three times | `failed`, key released | the model is told; the decision goes to a person (R2) |
| a turn dies with an incident `approved` or `executing` | as left | cleaning up the dead turn (§4) marks it `failed: outcome_unknown` and never calls again — a late call could page for a fault already resolved. The ticket gets a fallback decision toward a person; the next turn decides from a fresh status check |

Recovery of refunds runs at start-up for every unfinished one and ignores the lease, because with one
instance no executor can still be alive; and whenever an approval is posted again. There is no background
worker; the cost is in §13. With a real billing provider, "a refund made with our action id" means the
action id is written into the refund's metadata.

This replaces round 1's advisory lock: the claim is a row update, so the "lock" is visible in the table,
survives the connection, expires by itself, and needs no session held open across a provider call.

**What a hostile or confused model can do, at most.** Propose one refund per captured charge of this
customer, for the charged amount, each waiting for a person; a rejection stays rejected. Open one incident
per turn, which pages once unless that fault is already open. Nothing else is reachable from a tool.

## 7. The decision

The model fills in a **draft**; the service assembles the **decision**. The model is never asked to report
what it did, because its account would be a claim, not a record.

| Field | Draft (model) | Decision (service) |
|---|---|---|
| `urgency` | critical / high / medium / low | same, or `null` on fallback |
| `product_area` | `billing`, `account`, `availability`, `app`, `integrations`, `other` | same |
| `issue_type`, `additional_issue_types` | `outage`, `bug`, `billing_dispute`, `how_to`, `feature_request`, `account_change`, `other` | same |
| `sentiment` | negative / neutral / positive / mixed | same |
| `language` | language code of the customer's messages | same |
| `next_action`, `route_to` | one of the ranked list; when routing, one of `billing`, `technical`, `account` | after the rules below |
| `rationale` | English, cites message numbers and tool results | same |
| `evidence_conflicts` | list of source / claim / contradicted by | same |
| `kb_article_ids` | ids it relied on | only ids a search actually returned |
| `customer_reply_draft` | in the customer's language, or null | same — a draft, never sent |
| `operator_reply` | answer to the operator, or null | same |
| `tools_called` | — | from the recorded steps: name, arguments, ok, duration |
| `actions` | — | id, kind, status of every action this conversation proposed or joined |
| `source`, `fallback_reason`, `overrides`, `changed_fields`, `model`, `prompt_version`, `version` | — | set by the service |

Strict structured output: every field required, optional means nullable, closed lists as enums. The draft
schema is complete from the first build step, so it does not change after the evaluation exists.

**Next actions are ranked** by how far they put the ticket in front of a person:

| Rank | Action | A person reads the ticket first |
|---|---|---|
| 0 | `auto_respond` | no |
| 1 | `route_to_specialist` | yes |
| 2 | `escalate_to_human` | yes, now |

**Rules** are floors: the final action is the highest of the draft and every floor that fired, so no rule
can undo another and none can move away from a person (D5). Each one that changed the result is recorded in
`overrides`.

| Rule | Condition | Floor |
|---|---|---|
| R1 | an action this conversation proposed or joined is not in a final status | `escalate_to_human` |
| R2 | this conversation has an incident action created since its last completed decision — unless that action's first attempt was answered "already open" | `escalate_to_human` |
| R3 | the draft's action has no person reading first, and: no article that a search returned is cited, or a conflict is listed, or urgency is high or critical, or there is no reply draft | `route_to_specialist` |
| R4 | the draft's action has no person reading first, and a read tool failed in this turn without a later success | `route_to_specialist` |

R2 reads our own rows, not the pager's last word: a ticket whose first call paged and lost the answer
still counts as the one that opened it, and so does a failed attempt. A ticket that plainly joined an open
incident gets no floor: forty-five tickets about one outage are one page, not forty-five interruptions.
One test asserts that every next action has a rank.

Urgency is never changed by code. If the model's urgency disagrees with what it did (an incident with
urgency "low"), that is visible in the stored decision. Not built: no code logs it and the evaluation does
not count it — an inconsistency to measure, not to cover.

## 8. The prompt

`backend/application/prompts/triage_v2.md` (v1 is kept beside it; both are measured in `eval-baseline.md`), loaded at start-up; its version plus a short content hash is stored on
every decision. Comments explaining *why* each section exists sit beside the section and are stripped before
sending. Sections, in order: what triage is and is not; who speaks and how far their words go; the urgency
definitions; the next-action definitions; evidence — sources, age, and what to do when sources disagree;
effects — what may be proposed, what happens to a proposal, what is not available here; working with the
operator; the output contract.

It contains the definitions of Brief §3 and nothing about the three sample tickets, which are the test set.
Reply drafts say a refund has been *requested and is awaiting review*, never that it was made.

Input each turn is built from the database: the time now; customer metadata; the thread with message
numbers, authors and ages; the previous decision; the actions and their current statuses; the operator
conversation so far. The thread is passed as clearly delimited data. That delimiting is the second line —
the first is that nothing a customer can make the model do exceeds §6's bounds. Input over a size cap is not
truncated silently: the turn ends in a fallback toward a person.

## 9. What goes wrong, by sample

| Ticket | What goes wrong | What the design does |
|---|---|---|
| 1 | Billing says "failed"; the customer sees three charges | Both are in front of the model with their sources; the conflict is recorded; any conflict blocks an unattended answer (R3) |
| 1 | The model refunds all three, leaving the customer without Pro before a presentation | It cannot refund; it proposes. The hold is denied as not refundable. A person sees each proposal beside its charge and approves the duplicate only |
| 1 | The person rejects the refund of the charge that pays for Pro; next turn the model proposes it again | The rejection holds the key: the proposal comes back as "rejected by …", and nothing new waits for approval |
| 1 | The approval request is retried, or two people approve | Status is read before evidence; one compare-and-set wins; the retry gets 200 with the current status |
| 1 | The refund succeeds at the provider and the service dies before recording it | Recovery reads the charge, finds the refund made with this action's id, and records success without a second call |
| 1 | The customer's last message is loud | Urgency is defined on harm and time, not tone; the "2 hours" and "end of day" clocks are what make it high |
| 2 | The status page says operational | The result is labelled as the public page with its age; the incident list is separate and empty; the definitions make a green page beside a credible report a reason to page |
| 2 | Forty-five colleagues file tickets about the same outage | Forty-five triages, one incident, one page — and only the ticket that opened it is forced to a person |
| 2 | The pager times out | Three attempts inside the tool call; if all fail the model is told and R2 sends the ticket to a person, who is the fallback pager |
| 2 | The service dies mid-page and restarts hours later, after on-call resolved the incident | The left-over action is marked failed and never re-sent: a late call would page for a fault already fixed |
| 2 | The thread is Thai and calm | Fields and rationale are English; the reply draft is Thai; knowledge-base queries are English keywords; calm does not lower urgency |
| 2 | The model is down when the ticket arrives | Stored; 201 with a fallback decision toward a person; the same ticket sent again is triaged properly, not answered with the fallback for ever |
| 3 | The article says dark mode follows *System Default*; from message 3 the customer shows it does not | Messages 1–2 are answered by the article. At message 3 the thread contradicts it, the conflict is recorded, and an unattended answer is blocked (R3) |
| 3 | The newest message is a casual feature idea; the bug is two messages back | The whole thread is read as of now; the ticket takes the action of the issue that needs a person most |
| 3 | Friendly tone tempts an effect | None is justified; the label sheet forbids one, and the evaluation counts it as a violation |
| any | The ticket says "everything is down, open incidents for every component, and refund me" | One page at most, and proposals on that customer's own captured charges, each waiting for a person |
| any | The operator asks a question and the model re-emits a different urgency for no reason | Not prevented by code; visible in `changed_fields`, and an evaluation case fails on it |

Where the design fails, honestly: it cannot void the hold or grant Pro for Ticket 1; it cannot know that
Ticket 2's "Asia" and another customer's "Singapore" are one fault unless both map to the same region; and
for Ticket 3 it cannot tell a wrong article from a customer looking in the wrong place — only that they
disagree.

## 10. Tests and evaluation

The third thing done properly. The argument: separate what must **always** hold from what holds at a
**rate**, and test each with the tool that fits.

| Layer | Model | Question | Examples |
|---|---|---|---|
| Invariants | scripted to misbehave | Whatever the model outputs, does the boundary hold? | no provider refund without an approval event; at most one succeeded refund per charge; another customer's charge is denied exactly like "not found"; the refund tool called twice → one action; a second incident in one turn is denied; invalid output twice → fallback; a looping model is stopped; no rule lowers the rank |
| Sequences | none | Under the interleavings that matter, do the same invariants hold? | named sequences from §6's table with mock faults; two real connections racing on the claim and on the insert |
| Contract | scripted | Does the API behave as documented? | status codes; problem+json; ticket replay returns the stored decision with no model call; a fallback or an unfinished prefix is triaged on replay; an operator message with the same key is not stored twice; 409 while busy; state readable after a restart |
| Evaluation | real | How often is the triage right, and is it stable? | the 12 prefix cases of Brief §4; two operator cases; adversarial cases |

**Evaluation**, off the serving path: `python -m evals.run [--case …] [--runs N] [--prompt STEM]`.

- `create_app(llm, providers, clock, settings, load_sample_data, prompt)` is the only place a model, the providers,
  the clock, the sample data and a prompt other than the shipped one enter. Tests
  and the evaluation drive the application through the in-process test client — with the scripted or the
  real model, mock providers they can set faults on, and a fixed clock — so they pass through the same
  validation and replay code as a real request. The served application passes the real ones.
- The evaluation and the tests each use their own database, dropped and recreated at the start of a run;
  the evaluation refuses to start on the served database.
- Before every run of every case it empties all tables except the knowledge base and loads that case's
  **scenario**: the provider state as of that prefix — Ticket 1 after message 1 has one failed attempt,
  after message 2 two holds, after message 3 two captured charges and a hold; Ticket 2 has a green page and
  no open incident; Ticket 3 is a Pro account with no charges. Every case's customer has an account.
- Time is fixed per case: "now" is the moment the prefix's last message arrived, message times are offsets
  from it, and the mocks use the same clock. The routes read the same injected clock; only the leases run on
  the database clock.
- A forbidden effect means an action of that kind was *created*; a denied attempt creates none and is not
  counted (reporting it separately was designed and is not built).
- Two operator cases: a question, after which urgency and next action must equal the previous decision's;
  and a correction ("this is high, not critical"), after which the decision changes and the rationale cites
  the operator. Adversarial cases built: instructions inside the thread, and a read tool down. Designed and
  not built: no article found, a calm critical ticket, a loud low one.
- Each case runs three times. Reported per field: pass / miss / violation; plus effect violations, required
  tools missed, disagreement between runs, latency and tokens. Any forbidden value or forbidden effect fails
  the run. The printed table is committed as the baseline for the next prompt or model change.

The prompt is tuned against the same twelve cases it is reported on. They show the harness has the right
shape, not that the model generalises.

**In production** (`L62`), from tables that already exist: the share of decisions that are fallbacks; how
often each rule overrode the model; urgency-versus-effect inconsistencies; the share of refund proposals a
person rejects or that went stale; and operator turns after which the decision changed. Each of the last
two is a label made by a person, and is copied into the labelled set. All of it is sliced by prompt version
and by the model id the API returned, so a vendor-side model change shows up as a new id; a new id or a new
prompt hash has to pass the baseline before it serves.

Not built. Last in the build order, and the first to go if time runs out: a property-based test over generated
sequences, and a judge model that scores whether the rationale is supported by the tool results it cites
and whether the reply draft promises anything nobody approved. The judge's scores are reported, not gating,
and it is not itself calibrated against hand labels — a stated limit.

Not tested: the vendor's model. The model fake stands at our own port, not at HTTP, because the SDK's
transport is not something we own.

A few end-to-end tests start the server as a separate process and repeat the contract over a socket — a
ticket, an operator turn, an approved refund, a kill between the approval and the refund — with the same
scripted model. The test command measures line and branch coverage of `backend/` and `main.py` and fails
below 95%. Where each kind of test lives and what none of them covers: `testing.md`.

## 11. Observability and configuration

- One JSON log line per event — `turn.started`, `llm.call`, `tool.call`, `action.transition`,
  `decision.made`, `turn.fallback` — each with request, conversation, turn and action ids, duration and
  outcome. Message bodies and tool payloads are not logged at the default level; they are in `steps`, and
  `GET /conversations/{id}` returns them. A decision is reconstructed from the database, and the logs lead to
  it by id.
- No tracing dependency. OpenTelemetry's conventions for model calls are still marked as in development.
- Settings come from the environment: `OPENAI_API_KEY`, `TRIAGE_MODEL` (default `gpt-4.1-mini`, the
  owner's choice; changed if the evaluation shows it is not good enough), `TRIAGE_REASONING_EFFORT`
  (optional; sent only when set, because a non-reasoning model rejects it), `DATABASE_URL`,
  `TRIAGE_LOG_LEVEL`. The key is optional at start-up, so the service, `/healthz` and the
  test suite run without one; a request that needs the model then gets the 503 of §3. No key in the
  repository; an example file lists names only.
- Toolchain: Python 3.13 (`python:3.13-slim`), pip with pinned requirements, `psycopg[binary,pool]`.
  Everything runs in the image, so a reviewer needs Docker and nothing else. The service mounts the
  repository, so a code change needs no rebuild; the database port is not published.
- One command: `OPENAI_API_KEY=… docker compose up --build` starts Postgres and the service, applies the
  schema and loads the seed data. Compose passes the key through from the caller's environment or env file.
- Seed data is the knowledge base plus the full-thread provider state of three named customers, loaded by
  `infrastructure/providers/sample_data.py` — the module the evaluation's scenarios load each prefix from —
  with times as offsets from start-up. `python -m evals.sample t1|t2|t3`
  prints the matching request body with those customer ids and current timestamps; the README pipes it to
  curl.
- Tests run with `docker compose run --rm app pytest` and need no key. Anything that calls the real model —
  a sample request, the evaluation, `python -m evals.smoke` — is run by whoever holds the key.
  `python -m backend.cli.reset` rebuilds the served database after a schema change.

## 12. Build order

This section is the plan as it was written before the build, kept unchanged below. Built: S0 to S7. Not
built: S8, and the adversarial cases beyond injection and tool-down.

Round 1 built inner layers first and never reached the model. This order ends every step with something
that runs from HTTP to the model and back, and a commit. What the assignment requires comes before what
only deepens a proof.

| Step | Ends when | Estimate |
|---|---|---|
| S0 | by hand, before the pipeline: image, compose, pinned requirements, `/healthz`, one passing test; the `PROBE` questions answered with a real key; the real model adapter makes one strict tool call and one strict final answer | 0:45 |
| S1 | `POST /tickets` with Ticket 1 → loop with `billing_lookup` → full draft schema → stored decision → `GET` shows it, proven by a test with the scripted model; a README with the one command and three sample lines. Not yet: lock, the lease check before an effect, rules, problem+json, replay | 2:30 |
| S2 | three read tools, knowledge base, per-prefix scenarios, the clock; R3 and R4; the evaluation runs the 12 cases and prints a table (`open_incident` is reported missing for T2-2 to T2-4 until S4) | 2:30 |
| S3 | operator turns through the same loop, with their two evaluation cases; the two-page summary drafted from the brief and this document | 1:15 |
| S4 refund | policy, keys, propose, approve, execute; R1; the refund invariants and the two-connection races pass with the scripted model | 2:30 |
| S4 incident | `open_incident` in-line with its mock; R2; one per turn; opened floors to a person, joined does not | 1:15 |
| S5 | ticket replay, operator message key, busy and the lease check before an effect, dead-turn clean-up, problem+json, restart test | 1:30 |
| S5b | the "it breaks here" table with mock faults; `needs_review` and resolution; refund recovery at start-up | 1:30 |
| S6 | prompt revised against the evaluation with the adversarial cases; baseline committed; logs checked against §11 | 2:00 |
| S7 | README complete; summary final at two pages; a fresh clone runs with the one command | 1:00 |
| S8 | property-based sequences; judge | 1:30 |
| — | pushed to a public GitHub repository — the owner's action (`L69`) | |

About 16:45 to a complete submission, 18:15 with S8. Every step has a check the builder can run with the
scripted model, and a separate real-model check run by whoever holds the key. A step that overruns its
estimate by half is reported, not absorbed. If time runs out, things fall off from the end: S8 first, then
the adversarial cases beyond injection and tool-down, then resolution, then the rest of S5b. Never dropped: operator turns, the README and its
one command, the two-page summary, the comments in the prompt.

## 13. Costs accepted

| Choice | Cost | With another week |
|---|---|---|
| Synchronous turn inside the request | A request lasts as long as the model takes; about 40 turns at once | accept the ticket, answer 202, process on a queue; the turn runner is already separate from the routes |
| No streaming (`L37`, a plus) | The operator waits for the whole turn | stream the reply text; the decision still arrives only after validation and the rules |
| One instance | Every exclusion is a compare-and-set in Postgres and would hold across instances, but start-up recovery assumes nothing else is running | a worker that owns unfinished actions |
| No background worker | A stuck refund waits for start-up or a re-posted approval; an unanswered approval never times out | the same worker, with a due time that pages on expiry |
| Full refunds of captured charges only | Holds, partial refunds and plan changes go to a person as text | void and partial refund as further kinds |
| A rejected refund cannot be re-opened here | A person who changes their mind refunds at the provider | an explicit re-open by a person |
| A refund that settles days later and then fails | `succeeded` is final here | resolution on a succeeded action |
| The pager is assumed to dedup while open | Without that, every turn that opens an incident would page | own the lifetime as for refunds: key `incident:{component}:{region}`, held until the status check no longer lists it. Until then a second page is accepted — it can be taken back; a missed one cannot |
| The billing provider is assumed to honour keys | Reading the charge first covers a provider that does not, for full refunds; partial refunds would not be safe | per-provider reconciliation |
| Lexical search over English | Misses paraphrase; relies on the model turning the question into keywords | embeddings; measure retrieval separately from triage |
| The model picks an incident's component | Separate runs may pick different components for one fault: at worst one page per listed component (four) per region per open fault | derive the component from the status check or from a routing table, not from the model |
| No request key on ticket ingest | A sender who omits the ticket id and retries gets a second conversation and a second triage — but no second effect | a request key stored with the conversation |
| Fault identity is component × region | Two faults in one component and region fold into one incident; one fault seen from two regions makes two | correlation owned by the incident system |
| Approver is a name in the request | Not verified | authentication |
| Operator corrections are honoured by the model reading history | Not pinned by code | a stored override the rules respect |
| Schema applied from one file at start-up | No migration history | a migration tool once the schema has a second version |
| Twelve labelled cases, tuned on | Shows shape, not level | a held-out set fed by production corrections |

## 14. Where each requirement is met

| Assignment | Section |
|---|---|
| Urgency, extracted fields, knowledge-base search, next action (`L11`–`L14`) | §5, §7, §8 |
| Two tools, one with a real side effect, realistic contracts (`L15`, `L27`) | §5, §6 |
| Conversational, state persists (`L16`, `L36`–`L39`) | §2, §3, §4 operator turn |
| Prompt as code (`L26`) | §8 |
| Structured decision, tools called and why (`L28`) | §7 |
| Autonomy boundary, in code and in the two-page summary (`L29`) | §6 policy table |
| Stack chosen and justified (`L34`, `L68`) | §0 D4, D7 |
| Idempotency and safe retries (`L40`) | §3 replays, §6 identity and "it breaks here" |
| Validation, status codes, error shapes, secrets (`L45`) | §3, §11 |
| Test strategy for a non-deterministic core (`L50`) | §10 |
| Offline evaluation with labels (`L51`) | §10, Brief §4 |
| Decisions reconstructable (`L52`) | §2 `steps`, §11 |
| One-command setup, README (`L53`, `L70`) | §11, built in S1 |
| Two-page summary, delivered outside the repository: architecture, cuts, ticket-specific failures, production evaluation (`L59`–`L62`) | §0; §13 and Brief §10; §9; §10 "In production" |
| OpenAI model, key from the environment (`L67`) | §11, §3 "When the model fails" |
| GitHub repository (`L69`) | §12 last row |
