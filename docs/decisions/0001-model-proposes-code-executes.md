# 0001 — The model proposes, code executes

Source: `design.md` §0 D1 and §6.

## Context

The service triages tickets with a model whose output cannot be repeated, and two of the things a ticket can
lead to — a refund and a page to on-call — must not happen twice or without the right approval. A model that
reads customer text can be told by that text to do anything its tools allow.

## Decision

The model has no tool that reaches a money-moving provider. Its effect tools (`propose_refund`,
`open_incident` in `backend/application/tools/`) create an **action**; one table, `POLICY` in
`backend/domain/policy.py`, says who approves each kind; and one driver, `backend/application/actions/execute.py`,
performs it. There is one path for every effect kind:

```
propose -> pending_approval -> approved -> executing -> succeeded | failed | needs_review
```

A refund is approved by a person through `POST /actions/{id}/approval`. An incident is approved by its policy
row (`policy:incident`), at most one per turn. A kind with no row needs a person (`UNLISTED`).

## Consequences

- No model output can move money: the worst a hostile or confused model can do is propose one refund per
  captured charge of the ticket's own customer, each waiting for a person, and open one incident per turn.
- Amounts and customer ids never come from the model; `refund.propose` copies them from the charge.
- "Refunds under some amount are automatic" is a change to one policy row, not to the model's tools.
- A refund needs a second request from a person before anything happens.
- `tests/integration/test_refund_invariants.py` holds the boundary with a model scripted to misbehave.

## Rejected

Giving the model a tool that performs the refund, with or without an idempotency key of its own: a key the
model invents is new on every turn, and the approval would be the model's own claim.
