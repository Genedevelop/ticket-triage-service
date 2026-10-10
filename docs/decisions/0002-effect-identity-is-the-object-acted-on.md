# 0002 — An effect is identified by the object it acts on

Source: `design.md` §0 D2 and §6 "Identity".

## Context

The model decides again on every turn, a sender may retry a request, and two conversations may be about the
same charge or the same fault. An identity taken from the request, the turn or the model's tool call would be
new each time, so it could not stop a second refund.

## Decision

An action's key names the thing acted on. The functions are in `backend/domain/effect_keys.py`:

| Kind | Our key | What the provider is given |
|---|---|---|
| refund | `refund:{charge_id}` | the action's id, as idempotency key |
| incident | `incident:{conversation_id}:{component}:{region}` | `{component}:{region}`, as dedup key |

The key is unique among the actions that hold it, by a partial unique index in
`backend/infrastructure/postgres/schema.sql`:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS actions_live_key ON actions (key)
 WHERE status IN ('pending_approval', 'approved', 'executing', 'needs_review')
    OR (hold_key AND status IN ('succeeded', 'rejected'));
```

`hold_key` is copied from the policy row. A refund keeps its key after success and after a person's
rejection; an incident holds it only while in flight, because the incident system owns open-versus-resolved.
A proposal that finds the key held joins the existing action and writes a `joined` event
(`postgres/actions.py::propose`).

## Consequences

- One refund action per charge, whichever ticket, turn or conversation proposes it; a rejected refund stays
  rejected.
- A sender who retries with no ticket id gets a second conversation and a second triage, and still no second
  effect.
- The one model-chosen part of any identity is an incident's `component`, from a closed list of four
  (`COMPONENTS` in `backend/domain/ports.py`). Separate runs may pick different components for one fault.
- Two faults in one component and region fold into one incident.
- `tests/integration/test_refund_races.py` runs the insert and the claim on two real connections.

## Rejected

A key per request or per tool call.
