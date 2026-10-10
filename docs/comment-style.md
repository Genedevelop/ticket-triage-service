# Comment style

Where a comment goes depends on where the code is. The rules below are the ones applied across the
repository; the examples are from it.

## Inside a function, in `backend/`, `main.py` and `evals/`: `# Step N:`

A function whose body has two or more distinct steps gets one comment per step:

- exactly `# Step N: <sentence>` — English, one line of at most 110 characters;
- numbered from 1 inside each function (a nested function starts again at 1);
- placed directly above the step; the arms of a branch (`if`/`else`, `except`) are numbered in the order they
  are written, like steps that follow one another;
- a reason that is not obvious is folded into the same line.

A function with a single step gets no comment. There are no other comments inside a function body.

Good — `backend/application/agent_loop.py`:

```python
def run_tool_calls(self, calls: tuple[ToolCall, ...], items: list[Any]) -> None:
    for call in calls:
        # Step 1: Re-check the lease before an effect tool, because only the turn holding the conversation fires one
        tool = REGISTRY.get(call.name)
        ...
        # Step 2: Run the tool and record the call in the audit trail and the log
        started = monotonic()
        ...
        # Step 3: Hand the result to the model as its next input item, in the vendor's shape
        items.append(self.llm.tool_result(call.call_id, result))
```

Bad — unnumbered, restating the code, with the reason on a second line:

```python
# check lease
# (we do this because a turn that ran past its lease must not fire an effect)
tool = REGISTRY.get(call.name)
```

Good — a single-step function, no comment (`backend/application/tools/status_check.py`):

```python
def _status_check(_: NoArguments, context: ToolContext) -> dict[str, Any]:
    try:
        return context.providers.status.check(context.now)
    except ProviderError as error:
        raise ToolError.from_provider(error) from error
```

## No docstrings

Functions, methods, classes and modules have no docstring. The name explains the thing; when it does not,
the name is changed.

Bad — what `backend/core/logging.py` had before:

```python
class _Stdout(logging.Handler):
    """Writes each line to whatever standard output is at that moment."""
```

Good — what it has now:

```python
class _CurrentStdoutHandler(logging.Handler):
```

The text FastAPI shows for a route is not a docstring either; it is `description=` on the decorator:

```python
@router.get("/healthz", description="Report that the process is up and whether the database is reachable.")
```

## At module level, in `backend/`, `main.py` and `evals/`: one plain line

A constant, a SQL statement, a row of a table, or a field of a dataclass or Pydantic model may carry one
plain `#` comment directly above it, in lower case, when its reason is not in its name.

Good — `backend/domain/policy.py`:

```python
POLICY: dict[str, Policy] = {
    # money cannot be taken back by us: a person approves any amount, and an unknown outcome goes to a person
    "refund": Policy(approver=PERSON, is_key_held=True, parked="needs_review"),
```

Good — a field (`backend/core/config.py`):

```python
# sent only when set, because a non-reasoning model rejects the parameter
reasoning_effort: str | None
```

Bad — a comment that repeats the name:

```python
# the maximum number of steps
MAX_STEPS = 8
```

A package marker (`__init__.py`) opens with exactly one such line, saying what the package is and, for a
layer under `backend/`, what it may import. The same holds under `evals/` and `tests/`.

Good — `backend/core/__init__.py`:

```python
# what every layer shares: the settings, the one logger and the error hierarchy; imports nothing outside itself
```

Checked for `backend/` by `tests/architecture/test_package_markers.py`.

## Tests

No `# Step N:`. The test's name is the explanation. A helper, a fixture or a module may carry one plain line
above it where the set-up is not obvious:

```python
# let every conversation's lease run out by moving its end into the past, instead of waiting for it
def expire_lease(database_url: str) -> None:
```

## Root and configuration files

`.gitignore`, `.dockerignore`, the example environment file, `compose.yaml`, `Dockerfile`, `pytest.ini`,
`.coveragerc`, `requirements.in` and `schema.sql` use plain comments grouped under category headers.

Good — `.gitignore`:

```
# --- Secrets ---
# never committed; the example file lists the key names without values
```

`schema.sql` uses the SQL form of the same header:

```sql
-- --- Turns, steps and decisions ---
```

## Prompt files

`backend/application/prompts/*.md` keep a `<!-- Why: ... -->` comment beside each section. These are stripped by
`model_input.load_prompt` before the prompt is sent, so they change neither the prompt nor its hash. Do not
reformat them.

## Never in a comment

Task ids, ticket keys, review round numbers, design section numbers, or notes about how the code came to be.
That belongs in the commit message.

## How it is checked

Apart from the package markers, there is no committed test for comment style; it is checked by review. Three commands help:

```sh
# docstrings: every hit must be the delimiter of a SQL string
grep -rn '"""' backend main.py evals

# plain comments: every hit must sit at module level, above a field, or inside a module-level table
grep -rn '^\s*# ' backend main.py evals | grep -v '# Step [0-9]*: '

# step comments that are not one sentence starting with a capital
grep -rnE '# Step [0-9]+: [^A-Z]' backend main.py evals
```
