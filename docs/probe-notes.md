# Probe notes — what the real API does

Run on 2026-10-10 with `openai` 3.27.0 on Python 3.13, Responses API, `store=False`,
`parallel_tool_calls=False`. These answer the `PROBE` marks in `design.md`.

| Question | Answer |
|---|---|
| Strict function tools and a strict JSON-schema final answer in one request | Works on `gpt-4.1-mini`. Step 1 returned one `function_call`; step 2 returned a `message` whose text parsed with every required key and no extra one. |
| What is carried between steps without server-side state | The response's output items are appended to the input, followed by one `function_call_output` per call. Nothing else is needed for `gpt-4.1-mini`: it returns no reasoning items. |
| One call per step | Yes: one `function_call` in the step that called a tool. |
| Latency and tokens | A two-step turn took 3.8–6.1 s. Step 1: 347 input / 11 output tokens. Step 2: 521 input / 153–198 output. |
| Model id reported by the API | `gpt-4.1-mini-2025-04-14` — the dated id, not the alias. Stored on every decision, so a vendor-side change is visible. |
| A tool with no arguments | Accepted with an empty strict schema; called as `{}`. |

Run-to-run variation, seen in two runs of the same ticket: both chose `high` and `route_to_specialist`; one
listed an evidence conflict and one did not. That is the reason every evaluation case runs more than once.

## Errors, as the SDK raises them

| Situation | Exception | Status | Code | Class in the design |
|---|---|---|---|---|
| Model id does not exist | `NotFoundError` | 404 | `model_not_found` | misconfigured |
| Key has no access to the model | `PermissionDeniedError` | 403 | `model_not_found` | misconfigured |
| Wrong key | `AuthenticationError` | 401 | `invalid_api_key` | misconfigured |
| Account has no credit | `RateLimitError` | 429 | type `insufficient_quota` | misconfigured |
| Time-out | `APITimeoutError` | — | — | unavailable |

## Not answered

- Reasoning models: the key used had no access to `gpt-6-luna` (that first attempt left no log), so what must be
  carried between steps for a model that returns reasoning items is untested. The adapter sends a reasoning
  effort only when one is set.
  Tried again on the evening of 2026-10-10 with `gpt-5-mini` and reasoning effort `low`, through
  `python -m evals.smoke`: refused with `PermissionDeniedError 403 model_not_found`, the second row of the
  table above (`eval-logs/experiments/gpt-5-mini-smoke-refused.txt`). `gpt-4.1` was refused the same way
  (`eval-logs/experiments/triage_v2-gpt-4.1-refused.log`). The key reached `gpt-4.1-mini` only; a later
  attempt on `gpt-5-mini`, after access was requested, was refused with
  `RateLimitError 429 credit_balance_exhausted` (`eval-logs/experiments/gpt-5-mini-smoke-out-of-credit.txt`),
  so this is still untested against the vendor.
- Refusals and truncated answers were not provoked; the adapter treats both as an unusable answer.
- The SDK's default time-out is 600 s; the service always passes its own.
