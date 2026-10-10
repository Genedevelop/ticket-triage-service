from dataclasses import dataclass
from typing import Any, Callable

from backend.application.actions import incident, refund
from backend.core.errors import Internal
from backend.domain.ports import Providers


class EffectUnregistered(Internal):
    code = "effect-unregistered"
    title = "Effect unregistered"

    def __init__(self, kind: str) -> None:
        super().__init__(f"no effect is registered for the kind {kind}; nothing was approved or executed", details={"kind": kind})


@dataclass(frozen=True)
class Effect:
    # one attempt: the action, the providers and the time-out give a status, a provider reference and event detail
    perform: Callable[[dict[str, Any], Providers, float], tuple[str, str | None, dict[str, Any]]]
    # reads the evidence fields that changed since the proposal; a kind a person approves must have one, and an approval without it is refused
    changed_evidence_fields: Callable[[dict[str, Any], Providers, float], list[str]] | None = None


# one row per kind in the policy table
EFFECTS: dict[str, Effect] = {
    refund.KIND: Effect(
        perform=lambda action, providers, timeout_s: refund.perform(action, providers.billing, timeout_s),
        changed_evidence_fields=lambda action, providers, timeout_s: refund.changed_evidence_fields(action, providers.billing, timeout_s),
    ),
    incident.KIND: Effect(
        perform=lambda action, providers, timeout_s: incident.perform(action, providers.incidents, timeout_s),
    ),
}


def effect_for(kind: str) -> Effect:
    if kind not in EFFECTS:
        raise EffectUnregistered(kind)
    return EFFECTS[kind]
