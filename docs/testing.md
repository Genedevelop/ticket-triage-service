# Testing

Where a test goes, how the suite is run, what the coverage target is, and what no test here covers.
How a test is written — its name, no step comments — is in `code-style.md` §10 and `comment-style.md`.

## The one command

```sh
docker compose run --rm app pytest -q
```

It runs every folder under `tests/`, measures coverage, prints the files that have a line or a branch no
test ran, and **fails when the total is below 95%**. No key is needed. Never run two test commands at once:
they share one test database, which the run drops and recreates at its start.

The coverage tool is in `requirements.txt`, so the image must have been built since it was added
(`docker compose build app`). On an image built before that, the same suite runs with the tool installed for
that one run:

```sh
docker compose run --rm app sh -c "pip install -q --user pytest-cov==7.1.0 coverage==7.16.2 && python -m pytest -q"
```

## The four folders

A test lives in the folder named after what it touches. A subject seen at two levels is two files of the
same name, one per folder.

| Folder | A test belongs here when | It may use | It may not use |
|---|---|---|---|
| `tests/unit` | it calls a function or a class and reads what comes back | the code under test, stub objects written in the test, `monkeypatch` on a library the code calls | the database, the HTTP client, a process |
| `tests/integration` | it needs real PostgreSQL, or the application assembled by `create_app` and driven in process | the `client`, `llm`, `providers` and `empty_database` fixtures of `tests/conftest.py`; a second connection; threads | a port, a separate server process |
| `tests/architecture` | it states a rule about the code's structure | `ast`, the file tree, importing the modules to inspect their classes, synthetic source text for the negative case; `test_customer_record.py` also calls the two customer functions | the database, the application assembled by `create_app` |
| `tests/e2e` | it needs the service as its own process: uvicorn serving over a socket, or a command run as documented | the `serve` and `unscripted_server` fixtures of `tests/e2e/conftest.py`, `httpx`, the test database read from outside | anything of the server's memory: no fixture reaches into the process |

Run one folder alone by naming it and switching the coverage target off, which only the whole suite can
meet:

```sh
docker compose run --rm --no-deps app pytest -q --no-cov tests/unit
docker compose run --rm app pytest -q --no-cov tests/integration
docker compose run --rm --no-deps app pytest -q --no-cov tests/architecture
docker compose run --rm app pytest -q --no-cov tests/e2e
```

Which folder for which behaviour:

- A rule, a parser, a mapping from one value to another (`rules.apply`, the adapter's error classification,
  `action_status.check`): **unit**. The OpenAI adapter is tested against a stub client object, never the
  network (`tests/unit/test_openai_adapter.py`).
- Anything whose guarantee is a SQL statement — a compare-and-set, a unique index, a lease: **integration**,
  on real PostgreSQL, with two connections when the guarantee is about two callers
  (`tests/integration/test_refund_races.py`). There is no store double
  (`decisions/0006-tests-on-real-postgresql-without-a-store-double.md`).
- A route's status, headers and body: **integration**, through the in-process client. Each failure point
  between an approval and its execution: **integration**, with the mocks' fault modes
  (`tests/integration/test_it_breaks_here.py`).
- An interleaving no schedule produces on demand is injected, not waited for: a provider method that records
  the competing verdict before it answers, or a trigger on the test database that makes an insert return no
  row (`tests/integration/test_vanished_holder.py`).
- A rule about the code itself — who imports what, who logs, which exceptions exist: **architecture**.
- What only the real server shows — uvicorn's own answers, the lifespan at start and stop, a process that
  is killed: **e2e**.

## Property-based tests

The files `tests/unit/test_*_properties.py` state something that must hold for every input and let
`hypothesis` look for an input that breaks it. They sit beside the example-based file of the same subject;
the name of each test is the property.

| File | What it proves, for any generated input |
|---|---|
| `test_rules_properties.py` | the rules never leave a ticket further from a person than the draft did, under the real rules and under any table of rules, and never touch the urgency; applied to their own decision they change nothing and record nothing; the overrides recorded are exactly the rules that fired, each from the drafted action to its own floor, also when the answer cites a returned article and one cause alone, or none, is left to make a person read it; a decision differs from its draft only in the articles no search returned and in an overridden action |
| `test_cited_articles_properties.py` | the articles kept are exactly the cited ones that a successful step of a citable tool returned in that turn, in the order cited and as often as cited: an id cited twice is kept twice, nothing is sorted or made unique; an id only a failed step returned, or no step did, is dropped; with no citable tool, or no tool call, nothing is kept |
| `test_effect_keys_properties.py` | two refund keys are equal exactly when the charge is the same, in any order and however often proposed; the key given to the incident system is equal exactly when component and region are; our own incident key also carries the conversation, so two conversations hold two keys for one fault |
| `test_action_status_properties.py` | any sequence of steps moves only along `TRANSITIONS` and every other step raises `IllegalTransition`; nothing leaves a final status, the four being named in the test and not read from the table; every other status can reach one |
| `test_policy_properties.py` | every kind has a policy row; a kind the table does not list needs a person; no kind is approved both by a person and by its row |
| `test_strict_schema_properties.py` | every object of a strict schema forbids extra properties and requires each of its own, keeps every field name and every definition it refers to, and is unchanged when made strict again |
| `test_customer_record_properties.py` | a customer the ticket route accepts is read back from the two stored columns as sent; one sent without an id, or not sent, is read back with a null id |

**How the examples are generated.** From the real types, not from a list of samples.
`tests/unit/strategies.py` draws a `Draft` field by field from its annotations, its free text with a blank
at each end half the time; the steps of a turn with the
names of the registered tools and names no tool has, either outcome, and some calls taken again; actions in
every status of `action_status.STATUSES`; incidents with the events the incident rule reads; turns whose
last search returned the article cited, with a written reply three times in four and no conflict half the
time; and turns built so that cited and returned ids always meet: one successful search returning two to
four different ids, one failed search returning another, and a draft citing some of the first in a drawn
order, one of them twice, beside the failed search's id and ids no step returns. That last strategy exists
because free drawing rarely makes a cited id, a step that returned it and a citable tool meet: the general
property kept an article in 3 of its 200 examples when this was written and in none before, so four wrong
versions of the filter (nothing kept, everything kept, reversed, sorted and unique) passed. The test on
the built turns fails on each of the four and asserts in every example that something was kept; the general
one reports how often with `event`, shown by `--hypothesis-show-statistics`. The other files draw from the route's `CustomerIn`, from `COMPONENTS`, and from pydantic models
generated with scalars, closed lists of words, nulls, lists and nested models. The profile
`repeatable` in `tests/conftest.py` applies to every one: `derandomize=True`, so the examples are derived from the test itself and every run on
every machine draws the same ones; no example database; no time limit per example; 100 examples per test,
200 where the file says why. `hypothesis` still keeps a cache of its own in `.hypothesis/`, which git and
the image build ignore.

**How to reproduce a failure.** Run the test again: it draws the same examples and fails the same way, and
the report ends with the smallest input found, under "Failing test case".

```sh
docker compose run --rm --no-deps app pytest -q --no-cov tests/unit/test_rules_properties.py
```

To keep that input as a case of its own, put it in an `@example(...)` above the test or in the example-based
file of the subject. A property is checked on the examples drawn, not proven: a test whose body or strategy
changes draws other examples.

## End-to-end tests

`tests/e2e/conftest.py` starts `python -m uvicorn <factory> --factory` on a free port of `127.0.0.1`, with
the test database in `DATABASE_URL` and an empty `OPENAI_API_KEY`, and polls `/healthz` until it answers.
A server that exits because another process took the port first is started once more, on a port chosen again.
Requests are real HTTP over a socket, sent with `httpx`. After the test the server is stopped with the
terminate signal, and killed only if it does not stop in time; no process outlives its test. Nothing waits
for a fixed time: every wait polls a condition under a deadline (`wait_until`).

The factory is `tests/e2e/server.py::create_scripted_app`. It calls the real `main.create_app` and replaces
three things only: the model (the scripted model, its steps passed as JSON in `E2E_MODEL_SCRIPT`), the clock
(fixed) and, for the restart test, the faults queued on the billing mock (`E2E_BILLING_FAULTS`). The pool,
the schema, the knowledge base, the mock providers, the sample data and start-up recovery are the served
ones. One test starts the image's own factory, `main:create_app`, without a key.

| Test | What it proves |
|---|---|
| `test_the_served_application_answers_healthz_over_a_socket` | the server starts, the lifespan opened the database, uvicorn answers |
| `test_an_invalid_request_is_answered_with_the_validation_problem_document` | the `422` problem document, whole |
| `test_a_conversation_and_a_path_that_do_not_exist_are_answered_with_404_problem_documents` | the service's `404` and the framework's `404`, both as problem documents |
| `test_an_unhandled_failure_is_answered_with_the_servers_plain_text_500_and_the_server_keeps_serving` | an `Internal` error reaches uvicorn, which answers `500 Internal Server Error` as plain text and logs the exception |
| `test_a_server_asked_to_stop_runs_its_shut_down_and_exits` | the terminate signal runs the lifespan's shut-down |
| `test_the_real_factory_without_a_key_stores_the_ticket_and_answers_503_model_misconfigured` | the image's factory starts without a key, answers `503` `model-misconfigured`, and the thread is stored |
| `test_a_ticket_posted_over_http_is_decided_and_the_same_ticket_again_is_answered_from_store` | `201` with the decision, `X-Request-Id` echoed and on every log line of the request, then `200` with `Idempotent-Replayed: true` |
| `test_an_operator_message_over_http_is_answered_with_a_reply_and_a_new_decision_version_once_per_key` | an operator turn, and its `Idempotency-Key` |
| `test_a_proposed_refund_waits_for_a_person_and_an_approval_over_http_refunds_the_charge_once` | proposed, pending, approved over HTTP, succeeded, one refund in the provider's table |
| `test_a_server_killed_between_the_approval_and_the_refund_completes_the_refund_exactly_once_when_started_again` | the process is killed while the refund is claimed and not made; the next start makes it once |
| `test_the_reset_command_run_as_documented_drops_what_it_finds_and_leaves_the_empty_current_schema` | `python -m backend.cli.reset` as a process |
| `test_a_server_that_lost_its_port_is_started_once_more_on_another_and_serves` | the launcher itself: a first port held by another socket, a second start that serves |
| `test_a_server_that_loses_its_port_twice_is_not_started_a_third_time` | the retry happens once |
| `test_a_server_that_exits_early_for_any_other_reason_is_not_started_again` | only a lost port is retried |

They are few on purpose: each starts a process, which costs about a second. A behaviour that the in-process
client can show belongs in `tests/integration`.

### What no test here covers

- **The real model.** Every test here runs the scripted model, so nothing in `tests/` says whether a triage
  is right. That is measured by the evaluation (`python -m evals.run`, README "Evaluation"), by whoever holds
  the key, and recorded in `eval-baseline.md`. What the tests do cover is the evaluation's own code with the
  scripted model: the scoring, the printed header and table, and `--prompt`
  (`tests/unit/test_evals.py`, `tests/integration/test_evals.py`).
- **The vendor's API.** The adapter is tested against a stub client. Whether the vendor still accepts the
  request the adapter builds is checked by `python -m evals.smoke`, also with the key.
- **The image.** The tests run the repository mounted into the container, started by the tests' own command
  line. The `Dockerfile`'s `CMD` and `docker compose up` are not exercised.
- **Real providers.** Billing, the status page and the incident system are the mocks.
- **Load and concurrency over HTTP.** Races are tested in `tests/integration` on two connections, not by
  parallel requests to a server.

## Coverage

Measured with `pytest-cov` and `coverage.py`, configured in `.coveragerc`; `pytest.ini` switches it on for
every run.

- **What is measured**: the package `backend` and `main.py`, lines and branches. A server started by an
  end-to-end test is measured too and its data is combined with the run's (`patch = subprocess`); a server
  that is killed, as in the restart test, loses what it measured.
- **The target**: the total — lines and branches together — must be at least **95%**, or the test command
  fails. Do not lower the number.
- **`evals/` is measured and reported on its own**, outside the total: its entry points call the real model
  and cannot run in a test.

After a run of the whole suite:

```sh
# the lines and branches of each file that no test ran, as the run printed them
docker compose run --rm --no-deps app python -m coverage report

# evals, on its own
docker compose run --rm --no-deps app python -m coverage report --include="evals/*" --fail-under=0

# each file with something missing, line by line, written to htmlcov/index.html; a fully covered file is
# left out (skip_covered in .coveragerc), so at 100% the page lists none
docker compose run --rm --no-deps app python -m coverage html
```

Rules for keeping the number honest:

- A test asserts an outcome. A test that only calls code to make its lines run is not accepted.
- No `# pragma: no cover` and no `omit`. The only line that may ever be excluded is one that cannot run
  under test at all, and none is excluded today: even the `if __name__ == "__main__":` guard of
  `backend/cli/reset.py` is run, by the end-to-end test of the command.
- A line that cannot be reached is not excluded and not deleted quietly: say so, and let the owner decide.
- New code arrives with its tests. A recipe in `adding-a-feature.md` names the folder for each.
