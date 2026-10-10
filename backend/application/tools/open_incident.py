from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from backend.application.actions import execute, incident
from backend.application.tools.tool import Tool, ToolContext, ToolError
from backend.domain import action_status
from backend.domain.ports import COMPONENTS


class OpenIncidentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Literal[*COMPONENTS]
    summary: str


def _open_incident(arguments: OpenIncidentArguments, context: ToolContext) -> dict[str, Any]:
    # Step 1: Open the incident through the executor, or join the request in flight, telling the model a denial
    try:
        action, is_created = incident.open(
            context.pool,
            lambda action_id: execute.drive(context.pool, context.providers, action_id, context.timing),
            conversation_id=context.conversation_id,
            turn_id=context.turn_id,
            component=arguments.component,
            region=context.region,
            summary=arguments.summary,
            now=context.now,
        )
    except incident.IncidentDenied as denied:
        return {"denied": True, "reason": denied.reason, "message": denied.message}
    # Step 2: Tell the model that nobody was paged when the incident system refused, so a person must page on-call
    if action["status"] == action_status.FAILED:
        raise ToolError(
            "incident_not_opened",
            "the incident system did not open the incident and on-call was not paged by this call; "
            "it will not be tried again, so say in the rationale that a person must page on-call",
        )
    # Step 3: Report a request whose outcome is not known yet as still in progress
    action_summary = {"id": str(action["id"]), "kind": action["kind"], "status": action["status"], **action["payload"]}
    if action["status"] != action_status.SUCCEEDED:
        return {"denied": False, "action": action_summary, "incident": None, "already_requested": not is_created,
                "note": "This incident request is still in progress; nothing is known to be open yet."}
    # Step 4: Report the incident and whether it already existed, from the last answer of the incident system
    last_result = [event for event in action["events"] if event["type"] == "provider_result"][-1]
    return {
        "denied": False,
        "action": action_summary,
        "incident": {"id": action["provider_ref"], "already_existed": last_result["detail"]["already_open"]},
        "note": "An incident that already existed was joined, and nobody was paged again; a new one paged on-call.",
    }


OPEN_INCIDENT = Tool(
    name="open_incident",
    description=(
        "Open an incident for a fault in one component and page on-call, when the thread credibly reports a fault "
        "affecting many users or a whole customer and the status page shows no open incident for it. The region is "
        "taken from the ticket. If an incident is "
        "already open for that component and region, this ticket joins it and nobody is paged again. At most one "
        "per turn. Returns the incident id and whether it already existed, or a denial with the reason."
    ),
    arguments=OpenIncidentArguments,
    handler=_open_incident,
    is_effect=True,
)
