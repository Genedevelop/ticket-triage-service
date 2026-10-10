from backend.core.errors import Internal

PENDING = "pending_approval"
APPROVED = "approved"
REJECTED = "rejected"
STALE = "stale"
EXECUTING = "executing"
SUCCEEDED = "succeeded"
FAILED = "failed"
NEEDS_REVIEW = "needs_review"

TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({APPROVED, REJECTED, STALE}),
    # an incident left approved by a dead turn is failed without a call
    APPROVED: frozenset({EXECUTING, FAILED}),
    # a claim after a released or expired lease keeps the action executing
    EXECUTING: frozenset({EXECUTING, SUCCEEDED, FAILED, NEEDS_REVIEW}),
    NEEDS_REVIEW: frozenset({SUCCEEDED, FAILED}),
    REJECTED: frozenset(),
    STALE: frozenset(),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
}

STATUSES = frozenset(TRANSITIONS)
# statuses nothing leads out of
FINAL = frozenset(status for status, following in TRANSITIONS.items() if not following)


class IllegalTransition(Internal):
    code = "illegal-transition"
    title = "Illegal transition"


def check(current: str, following: str) -> None:
    if following not in TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(f"{current} -> {following}")
