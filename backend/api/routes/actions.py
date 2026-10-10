from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.application.actions import approve, resolve
from backend.core.errors import NotFound

router = APIRouter()


class ApprovalIn(BaseModel):
    verdict: Literal["approve", "reject"]
    # the person's name; not verified
    approver: str = Field(min_length=1)
    note: str | None = None


class ResolutionIn(BaseModel):
    outcome: Literal["succeeded", "failed"]
    # the person's name; not verified
    resolver: str = Field(min_length=1)
    note: str | None = None


@router.post(
    "/actions/{action_id}/approval",
    description="Record a person's verdict on a proposed effect; an approved one is executed before the answer.",
)
def approve_action(action_id: UUID, body: ApprovalIn, request: Request) -> JSONResponse:
    # Step 1: Record the verdict, executing an approved action before answering
    state = request.app.state
    action = approve.decide(
        state.pool,
        state.providers,
        action_id,
        verdict=body.verdict,
        approver=body.approver,
        note=body.note,
        timing=state.action_timing,
    )
    # Step 2: Report an action that does not exist, or answer with the action as it is now
    if action is None:
        raise NotFound("action")
    return JSONResponse(jsonable_encoder(action))


@router.post(
    "/actions/{action_id}/resolution",
    description="Record the outcome a person found at the provider for an action left in needs_review.",
)
def resolve_action(action_id: UUID, body: ResolutionIn, request: Request) -> JSONResponse:
    # Step 1: Record the resolution, which only an action left in needs_review accepts
    action = resolve.record(request.app.state.pool, action_id, outcome=body.outcome, resolver=body.resolver, note=body.note)
    # Step 2: Report an action that does not exist, or answer with the action as it is now
    if action is None:
        raise NotFound("action")
    return JSONResponse(jsonable_encoder(action))
