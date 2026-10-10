# 0005 — Rules move a decision only toward a person

Source: `design.md` §0 D5 and §7.

## Context

The model's draft cannot be trusted as it is: it may answer a ticket unattended while a refund waits for
approval, or cite an article no search returned. Free-form corrections by code would be a second, untested
decision maker.

## Decision

Next actions are ranked by how far they put the ticket in front of a person (`NEXT_ACTIONS` in
`backend/domain/rules.py`): `auto_respond` 0, `route_to_specialist` 1, `escalate_to_human` 2. Each rule is a
**floor**. `rules.apply` takes the highest of the draft and every floor that fired, and records in the
decision's `overrides` every rule that fired with a floor above the drafted action, each from the drafted
action to its own floor, also when a higher floor decided the result.

| Rule | Fires when | Floor |
|---|---|---|
| R1 | an action of this conversation is not in a final status | `escalate_to_human` |
| R2 | this conversation opened an incident since its last decision | `escalate_to_human` |
| R3 | an unattended answer cites no returned article, lists a conflict, is high or critical, or has no reply draft | `route_to_specialist` |
| R4 | an unattended answer follows a read tool whose last call failed | `route_to_specialist` |

Urgency is never changed by code.

## Consequences

- No rule can undo another, and none can move a ticket away from a person.
- A turn the model cannot finish still yields a decision: `decision.fallback` sets `escalate_to_human`.
- A rule cannot lower an over-cautious draft; a model that escalates everything is visible only in the
  evaluation.
- `tests/unit/test_rules.py` asserts that every next action has a rank and that no rule lowers it.

## Rejected

Trusting the draft, and free-form fix-up rules.
