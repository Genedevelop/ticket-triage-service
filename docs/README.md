# Documentation index

One line per document. The short way in is three pages: the root `../README.md` (run the service, send the
sample tickets, what each route answers), this index, and `architecture.md`.

## Suggested reading order

For a reviewer:

1. `../README.md` — run it and send the three sample tickets.
2. `problem-brief.md` — what the service has to do and the labelled cases it is measured on.
3. `architecture.md` — the layers, the import rules and the three request paths.
4. `eval-baseline.md` — what was measured on the real model, what the numbers say, and what is not fixed.
5. `decisions/` — why the design is what it is, one decision per file.
6. `design.md` — the full design, when a decision needs its detail.

For the next engineer, after the first three above:

7. `adding-a-feature.md` — recipes with the exact files and tests.
8. `code-style.md`, `comment-style.md`, `naming-conventions.md` — how code here is written.
9. `testing.md` — where a test goes, the coverage target, what the tests do not cover.
10. `logging-and-errors.md` — the event logger and the error hierarchy.

## The service

| Document | What it is |
|---|---|
| `problem-brief.md` | The problem: the three sample tickets, the definitions of urgency and next action, the label sheet of twelve prefix cases |
| `design.md` | The system design: data model, HTTP API, the turn, tools, the effect path, the decision and its rules, tests and evaluation, costs accepted |
| `eval-baseline.md` | The evaluation tables for each prompt version on the real model, what the numbers say, why the third version is not shipped, and what was not measured |
| `eval-logs/` | The terminal output behind the baseline, in three folders — `baseline/` (the first comparison), `shipped/` (the shipped prompt on the final code, and the README's requests answered by the served service), `experiments/` (the third prompt, and the models the vendor refused) — with the commands that count each number from it |
| `probe-notes.md` | What the real model API does, checked before the code depended on it |

## The repository

| Document | What it is |
|---|---|
| `architecture.md` | Layers and what lives in each, the dependency rules and the test that enforces them, the request paths, where state lives |
| `adding-a-feature.md` | How to add a read tool, an effect kind, a provider, a model vendor, a route, an intake channel, a decision field, a prompt version — and what would need wider change |
| `code-style.md` | The rules the code follows, with examples and how each is checked |
| `comment-style.md` | `# Step N:` inside functions, no docstrings, one plain line at module level, category headers in configuration files |
| `naming-conventions.md` | The case for every kind of name, from Python modules to problem type slugs |
| `testing.md` | The one command, what belongs in `tests/unit`, `integration`, `architecture` and `e2e`, the property-based tests, how the end-to-end tests serve the application, the 95% coverage target, what no test covers |
| `logging-and-errors.md` | Event names and keys, what is never logged, the error hierarchy with codes and statuses, how to add an error |

## Decisions

Each file has four parts: Context, Decision, Consequences, Rejected.

| Record | Decision |
|---|---|
| `decisions/0001-model-proposes-code-executes.md` | The model proposes an effect; a policy table says who approves; code executes |
| `decisions/0002-effect-identity-is-the-object-acted-on.md` | An effect is keyed by the charge or the fault, never by the request or the model's call |
| `decisions/0003-context-rebuilt-from-the-database-each-turn.md` | No conversation state in process memory or at the vendor |
| `decisions/0004-hand-written-loop-instead-of-a-framework.md` | The turn loop is ordinary code with its own deadline, step cap and fallback |
| `decisions/0005-rules-move-decisions-only-toward-a-person.md` | Rules are floors on the next action; none can lower it |
| `decisions/0006-tests-on-real-postgresql-without-a-store-double.md` | No fake store, and therefore no port in front of the store |
| `decisions/0007-synchronous-python-with-plain-sql.md` | FastAPI, psycopg 3, SQL strings, one schema file |
| `decisions/0008-layered-layout-with-enforced-imports.md` | Five layers, a composition root and a package of commands, checked by a test |
| `decisions/0009-one-error-hierarchy-mapped-once-to-problem-json.md` | Eight categories, one status table, routes that never catch |
| `decisions/0010-one-json-event-logger.md` | One module logs; every line has the same ids |
