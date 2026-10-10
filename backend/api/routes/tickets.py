from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field

from backend.api.runner import build_runner
from backend.application import triage_turn

router = APIRouter()


class CustomerIn(BaseModel):
    id: str | None = None
    plan: str | None = None
    region: str | None = None
    seats: int | None = None
    tenure_months: int | None = None
    prior_tickets: int | None = None


class MessageIn(BaseModel):
    id: str | None = None
    author: Literal["customer", "support_agent"]
    body: str = Field(min_length=1)
    sent_at: AwareDatetime


class TicketIn(BaseModel):
    ticket_id: str | None = None
    customer: CustomerIn | None = None
    messages: list[MessageIn] = Field(min_length=1)


@router.post(
    "/tickets",
    description="Store the thread, start or continue its conversation, and triage it; a completed triage is answered from store.",
)
def ingest_ticket(ticket: TicketIn, request: Request) -> JSONResponse:
    # Step 1: Store the thread and triage it, or take the decision already stored for it
    state = request.app.state
    outcome = triage_turn.ingest(
        build_runner(state),
        state.clock(),
        ticket_id=ticket.ticket_id,
        customer=ticket.customer.model_dump(exclude_none=True) if ticket.customer else None,
        messages=[message.model_dump() for message in ticket.messages],
    )
    # Step 2: Answer 201 for a new conversation and mark a decision that an earlier request stored
    body = {"conversation_id": str(outcome.conversation_id), "decision": outcome.decision}
    headers = {"Idempotent-Replayed": "true"} if outcome.is_replayed else None
    return JSONResponse(body, status_code=201 if outcome.is_created else 200, headers=headers)
