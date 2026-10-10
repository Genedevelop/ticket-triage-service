from dataclasses import dataclass

PERSON = "person"


@dataclass(frozen=True)
class Policy:
    # who must approve before code executes the effect
    approver: str
    # whether the key stays taken after success and after a person's rejection
    is_key_held: bool
    # the status an action is left in once its attempts are spent without an answer
    parked: str
    max_attempts: int = 3
    # how many of this kind one turn may propose; None means no limit
    per_turn: int | None = None


POLICY: dict[str, Policy] = {
    # money cannot be taken back by us: a person approves any amount, and an unknown outcome goes to a person
    "refund": Policy(approver=PERSON, is_key_held=True, parked="needs_review"),
    # an incident can be acknowledged and resolved, and a missed page is worse than a second one: the row itself approves
    "incident": Policy(approver="policy:incident", is_key_held=False, parked="failed", per_turn=1),
}

# anything not listed needs a person
UNLISTED = Policy(approver=PERSON, is_key_held=True, parked="needs_review")


def policy_for(kind: str) -> Policy:
    return POLICY.get(kind, UNLISTED)


def kinds_approved_by_a_person() -> tuple[str, ...]:
    return tuple(kind for kind, policy in POLICY.items() if policy.approver == PERSON)


def kinds_approved_by_their_policy() -> tuple[str, ...]:
    return tuple(kind for kind, policy in POLICY.items() if policy.approver != PERSON)
