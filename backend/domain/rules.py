from dataclasses import dataclass
from typing import Any, Callable, Collection, Sequence

from backend.domain import action_status


@dataclass(frozen=True)
class RankedAction:
    # how far the action puts the ticket in front of a person
    rank: int
    is_read_by_person_first: bool


NEXT_ACTIONS: dict[str, RankedAction] = {
    "auto_respond": RankedAction(rank=0, is_read_by_person_first=False),
    "route_to_specialist": RankedAction(rank=1, is_read_by_person_first=True),
    "escalate_to_human": RankedAction(rank=2, is_read_by_person_first=True),
}
URGENCIES_A_PERSON_MUST_READ = ("high", "critical")


def rank(action: str) -> int:
    return NEXT_ACTIONS[action].rank


def _unfinished_action_reason(decision: dict[str, Any], steps: list[dict[str, Any]], incidents: Sequence[dict[str, Any]]) -> str | None:
    # Step 1: Keep the actions of this ticket that have not reached a final status
    unfinished = [action for action in decision["actions"] if action["status"] not in action_status.FINAL]
    # Step 2: Give each one with its status as the reason
    return "; ".join(f"{action['kind']} {action['id']} is {action['status']}" for action in unfinished) or None


def _was_already_open_at_first_attempt(incident: dict[str, Any]) -> bool:
    # Step 1: Collect what the incident system answered at each attempt, oldest first
    results = [event["detail"] or {} for event in incident["events"] if event["type"] == "provider_result"]
    # Step 2: Read whether the first answer attached to an incident that was open already
    return bool(results) and results[0].get("already_open") is True


def _opened_incident_reason(decision: dict[str, Any], steps: list[dict[str, Any]], incidents: Sequence[dict[str, Any]]) -> str | None:
    # Step 1: Keep the incidents this ticket opened: a failed or lost first attempt counts, a plain join does not
    opened = [action for action in incidents if not _was_already_open_at_first_attempt(action)]
    # Step 2: Give each one with its status as the reason
    return "; ".join(f"incident {action['id']} was requested by this ticket and is {action['status']}" for action in opened) or None


def _unsupported_answer_reason(decision: dict[str, Any], steps: list[dict[str, Any]], incidents: Sequence[dict[str, Any]]) -> str | None:
    # Step 1: Leave alone a decision a person reads before anything goes out
    if NEXT_ACTIONS[decision["next_action"]].is_read_by_person_first:
        return None
    # Step 2: Collect everything that makes an unattended answer unsafe
    reasons = []
    if not decision["kb_article_ids"]:
        reasons.append("no returned article is cited")
    if decision["evidence_conflicts"]:
        reasons.append("a conflict is listed")
    if decision["urgency"] in URGENCIES_A_PERSON_MUST_READ:
        reasons.append(f"urgency is {decision['urgency']}")
    if not (decision["customer_reply_draft"] or "").strip():
        reasons.append("there is no reply draft")
    return "; ".join(reasons) or None


def _failed_read_tool_reason(decision: dict[str, Any], steps: list[dict[str, Any]], incidents: Sequence[dict[str, Any]]) -> str | None:
    # Step 1: Leave alone a decision a person reads before anything goes out
    if NEXT_ACTIONS[decision["next_action"]].is_read_by_person_first:
        return None
    # Step 2: Keep the outcome of the last call of each read tool, so a later success clears an earlier failure
    last_call_ok_by_tool = {step["name"]: step["ok"] for step in steps}
    # Step 3: Name the read tools whose last call failed
    failed = [name for name, is_ok in last_call_ok_by_tool.items() if not is_ok]
    return f"{', '.join(failed)} failed in this turn" if failed else None


@dataclass(frozen=True)
class Rule:
    name: str
    # the least the final action may be when the rule fires
    floor: str
    # why the rule fires for this decision, these calls of read tools and the incidents created since the last decision, or None
    reason_to_fire: Callable[[dict[str, Any], list[dict[str, Any]], Sequence[dict[str, Any]]], str | None]


RULES = (
    Rule("R1", "escalate_to_human", _unfinished_action_reason),
    Rule("R2", "escalate_to_human", _opened_incident_reason),
    Rule("R3", "route_to_specialist", _unsupported_answer_reason),
    Rule("R4", "route_to_specialist", _failed_read_tool_reason),
)


def apply(
    decision: dict[str, Any],
    steps: list[dict[str, Any]],
    incidents_since_last_decision: Sequence[dict[str, Any]] = (),
    *,
    read_tools: Collection[str],
) -> tuple[str, list[dict[str, str]]]:
    # Step 1: Start from the action the model drafted
    drafted = decision["next_action"]
    final = drafted
    overrides = []
    # Step 2: Keep the calls of the tools the caller names as read tools, the only steps a rule weighs
    read_steps = [step for step in steps if step["kind"] == "tool" and step["name"] in read_tools]
    # Step 3: Raise the action to the highest floor among the rules that fire, recording each override
    for rule in RULES:
        reason = rule.reason_to_fire(decision, read_steps, incidents_since_last_decision)
        if reason is None or rank(rule.floor) <= rank(drafted):
            continue
        overrides.append({"rule": rule.name, "from": drafted, "to": rule.floor, "reason": reason})
        final = max(final, rule.floor, key=rank)
    return final, overrides
