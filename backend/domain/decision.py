from typing import Any, Collection, Sequence

from backend.domain import rules
from backend.domain.draft import Draft

FALLBACK_ACTION = "escalate_to_human"


def changed_fields(previous: dict[str, Any] | None, body: dict[str, Any]) -> list[str]:
    if previous is None:
        return []
    return [name for name in Draft.model_fields if body[name] != previous[name]]


def _fields_recorded_by_service(
    steps: list[dict[str, Any]], actions: list[dict[str, Any]], model: str | None, prompt_version: str
) -> dict[str, Any]:
    return {
        "tools_called": [
            {"name": step["name"], "arguments": step["arguments"], "ok": step["ok"], "duration_ms": step["duration_ms"]}
            for step in steps
            if step["kind"] == "tool"
        ],
        "actions": [{"id": str(action["id"]), "kind": action["kind"], "status": action["status"]} for action in actions],
        "overrides": [],
        "changed_fields": [],
        "model": model,
        "prompt_version": prompt_version,
    }


def _article_ids_returned_by_search(steps: list[dict[str, Any]], citable_tools: Collection[str]) -> set[str]:
    return {
        article["id"]
        for step in steps
        if step["kind"] == "tool" and step["name"] in citable_tools and step["ok"]
        for article in step["result"].get("articles", [])
    }


def from_draft(
    draft: Draft,
    steps: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    model: str | None,
    prompt_version: str,
    incidents_since_last_decision: Sequence[dict[str, Any]] = (),
    *,
    read_tools: Collection[str],
    citable_tools: Collection[str],
) -> dict[str, Any]:
    # Step 1: Keep only the cited articles that a search returned in this turn, and add the service's own records
    returned = _article_ids_returned_by_search(steps, citable_tools)
    body = {
        **draft.model_dump(mode="json"),
        "kb_article_ids": [article_id for article_id in draft.kb_article_ids if article_id in returned],
        "source": "model",
        "fallback_reason": None,
        **_fields_recorded_by_service(steps, actions, model, prompt_version),
    }
    # Step 2: Apply the rules, which can only raise the action toward a person
    body["next_action"], body["overrides"] = rules.apply(body, steps, incidents_since_last_decision, read_tools=read_tools)
    return body


def fallback(
    reason: str, steps: list[dict[str, Any]], actions: list[dict[str, Any]], model: str | None, prompt_version: str
) -> dict[str, Any]:
    return {
        "language": None,
        "sentiment": None,
        "product_area": None,
        "issue_type": None,
        "additional_issue_types": [],
        "evidence_conflicts": [],
        "rationale": f"No triage was produced ({reason}); a person must read this ticket.",
        "urgency": None,
        "next_action": FALLBACK_ACTION,
        "route_to": None,
        "kb_article_ids": [],
        "customer_reply_draft": None,
        "operator_reply": None,
        "source": "fallback",
        "fallback_reason": reason,
        **_fields_recorded_by_service(steps, actions, model, prompt_version),
    }
